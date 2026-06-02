FROM node:22-bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY runner/package.json runner/tsconfig.json ./
RUN npm install

COPY runner/src ./src
RUN npm run build \
  && npm prune --omit=dev

RUN useradd --create-home --shell /bin/bash codex \
  && mkdir -p /home/codex/.codex /workspace /app/skills \
  && chown -R codex:codex /home/codex /workspace /app

USER codex
ENV HOME=/home/codex
ENV CODEX_HOME=/home/codex/.codex
ENV PORT=8789
EXPOSE 8789

CMD ["node", "dist/server.js"]

