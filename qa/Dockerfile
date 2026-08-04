# Self-contained Playwright runner: browsers + system deps baked in so we never
# depend on host libs or on a specific mcr.microsoft.com/playwright tag being published.
# Built and run by qa/run.sh; joins the compose network to reach the app by service name.
FROM node:24-bookworm

WORKDIR /qa

# Install JS deps first (cached layer), then the browser + its system libraries.
COPY package.json ./
RUN npm install \
 && npx playwright install --with-deps chromium

# Test sources are baked in (mounting would shadow node_modules). Rebuilds are fast:
# the npm+browser layers above are cached, only this COPY re-runs when specs change.
COPY playwright.config.ts ./
COPY ux ./ux

# Artifacts (screenshots, html report, json results) are written under /qa/artifacts,
# which run.sh bind-mounts to the host so results survive the container.
ENV CI=1
CMD ["npx", "playwright", "test"]
