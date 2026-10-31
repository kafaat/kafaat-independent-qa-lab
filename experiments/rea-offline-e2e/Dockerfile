# Experimental REA CLI only; not a production analyzer or Ghidra/Hopper provider.
# The GitHub runner builds this disposable image with no secrets in its build context.
FROM node:24-bookworm-slim
ENV npm_config_ignore_scripts=true npm_config_audit=false npm_config_fund=false
# REA requires a procps-compatible ps for Linux owned-process checks.
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/rea
RUN npm init -y >/dev/null && npm install --ignore-scripts --no-fund --no-audit --omit=dev --save-exact rea-agents@6.8.0
ENV PATH="/opt/rea/node_modules/.bin:$PATH"
USER node
ENTRYPOINT ["rea"]
