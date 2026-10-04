from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock

from kabubot.codex_client import _summary_prompt
from kabubot.config import load_settings
from kabubot.events import MarketEvent, analysis_batches
from kabubot.scanner import Scanner
from kabubot.types import GrokNarrative, PriceSignal


def test_event_analysis_uses_existing_pipeline_for_all_companies_and_keeps_scan_report(tmp_path, monkeypatch):
    settings = replace(load_settings(), data_dir=tmp_path, max_candidates=2,
                       discord_bot_token=None, discord_webhook_url=None, slack_webhook_url=None, xai_api_key=None)
    scanner = Scanner(settings)
    (tmp_path / 'latest.md').write_text('Existing market scan')
    (tmp_path / 'events' / 'latest.json').write_text('{"events": []}')
    events = [MarketEvent(symbol=f'S{i}', name=f'Full Company Name {i}', kind='earnings', day=date(2026, 10, 5))
              for i in range(13)]
    events.append(MarketEvent(symbol='S0', name='Full Company Name 0', kind='ex_dividend', day=date(2026, 10, 6)))
    quote_calls = []

    def quotes(symbols):
        quote_calls.append(symbols)
        return [PriceSignal(symbol=symbol, day_change_pct=-55 if symbol == 'S0' else 1,
                            price=100, currency='USD') for symbol in symbols]

    monkeypatch.setattr(scanner.yfinance, 'quote_symbols', quotes)
    scanner.grok.analyze = AsyncMock(return_value=GrokNarrative(enabled=True, summary='X evidence',
                                                               citations=['https://x.com/example/status/123']))
    scanner.codex.summarize = AsyncMock(return_value=('analysis ' * 700 + 'COMPLETE END', True))
    scanner.notifier.send = AsyncMock()

    def charts(report):
        paths = []
        for signal in report.top_signals:
            if signal.symbol == 'S12':
                continue  # Missing historical prices must be explicit in the reply.
            path = tmp_path / f'{signal.symbol}.png'
            path.write_bytes(b'png')
            paths.append(path)
        return paths

    monkeypatch.setattr(scanner.charts, 'render_report_charts', charts)

    async def run():
        return [await scanner.event_analysis({'sector_query': 'Software'}, batch)
                for batch in analysis_batches(events)]

    results = asyncio.run(run())
    assert quote_calls == [[f'S{i}' for i in range(10)], ['S10', 'S11', 'S12']]
    assert scanner.grok.analyze.await_count == scanner.codex.summarize.await_count == 2
    analyzed = [s for call in scanner.codex.summarize.await_args_list for s in call.args[1]]
    assert {s.symbol for s in analyzed} == {f'S{i}' for i in range(13)}
    assert all(s.name and 'Full Company Name' in s.name for s in analyzed)
    assert all(s.notes and any('決算予定' in note for note in s.notes) for s in analyzed)
    assert all(call.kwargs['event_context'] for call in scanner.codex.summarize.await_args_list)
    assert sum(len(paths) for _, paths in results) == 12
    assert all('COMPLETE END' in text and 'https://x.com/example/status/123' in text for text, _ in results)
    assert '株価推移画像を取得できませんでした: Full Company Name 12 (S12)' in results[-1][0]
    assert (tmp_path / 'latest.md').read_text() == 'Existing market scan'
    assert (tmp_path / 'events' / 'latest.json').read_text() == '{"events": []}'
    assert len(list((tmp_path / 'events' / 'analysis' / 'reports').glob('*.json'))) == 2
    scanner.notifier.send.assert_not_awaited()
    assert asyncio.run(scanner.event_analysis({'sector_query': 'Software'}, []))[1] == []
    assert len(quote_calls) == 2


def test_event_summary_prompt_carries_dates_and_requires_every_company():
    events = [{'symbol': 'TEST', 'name': 'Full Company Name', 'kind': 'earnings', 'day': '2026-10-05'}]
    prompt = _summary_prompt('Software', [PriceSignal(symbol='TEST')], GrokNarrative(enabled=False), 'ja', events)
    assert '2026-10-05' in prompt and 'upcoming_events' in prompt
    assert 'Cover EVERY supplied company' in prompt
    assert 'Write the event preview in Japanese' in prompt


def test_calendar_command_sends_all_pages_analysis_and_charts(tmp_path):
    from types import SimpleNamespace
    import discord
    from kabubot.discord_bot import KabuDiscordBot

    settings = replace(load_settings(), data_dir=tmp_path, discord_allowed_guild_id='123456789012345678')
    today = date(2026, 10, 4)
    events = [MarketEvent(symbol=f'S{i}', name=f'Full Company {i}', kind='earnings', day=today)
              for i in range(12)]
    snapshot = {'sector_query': 'Software', 'events': [e.model_dump(mode='json') for e in events],
                'symbols': [e.symbol for e in events], 'warnings': []}
    pages = [tmp_path / 'calendar.png', tmp_path / 'calendar-2.png']
    stock_chart = tmp_path / 'S0.png'
    for path in pages + [stock_chart]:
        path.write_bytes(b'png')
    monitor = SimpleNamespace(refresh=AsyncMock(return_value=snapshot), today=lambda: today,
                              calendar_paths=lambda data: pages)
    scanner = SimpleNamespace(events=monitor, event_analysis=AsyncMock(return_value=('Full analysis', [stock_chart])))
    sent, files = [], []

    async def send(content=None, **kwargs):
        sent.append(content)
        files.extend([f.filename for f in kwargs.get('files', [])])
        if kwargs.get('file'):
            files.append(kwargs['file'].filename)

    async def run():
        bot = KabuDiscordBot(settings, scanner)
        bot._interaction_allowed = lambda interaction: True
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()),
                                      channel=SimpleNamespace(send=send))
        command = bot.tree.get_command('calendar', guild=discord.Object(id=int(settings.discord_allowed_guild_id)))
        await command.callback(interaction)
        await bot.close()

    asyncio.run(run())
    assert files == ['calendar.png', 'calendar-2.png', 'S0.png', 'S0.png']
    assert sent.count('Full analysis') == 2
    assert scanner.event_analysis.await_count == 2
    assert all('失敗' not in text for text in sent if text)
