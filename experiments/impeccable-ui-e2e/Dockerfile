# Source-scanning experiment only; no browser or third-party UI execution.
FROM node:24-bookworm-slim
ENV npm_config_ignore_scripts=true npm_config_audit=false npm_config_fund=false
WORKDIR /opt/design
RUN npm init -y >/dev/null && npm install --ignore-scripts --no-audit --no-fund --omit=dev --include=optional --save-exact impeccable@4.8.0
ENV PATH="/opt/design/node_modules/.bin:$PATH"
USER node
ENTRYPOINT ["impeccable"]
