# Build from the REPO ROOT (`docker build -f apps/web/Dockerfile .`): pnpm
# is a workspace and `generate-client` needs the committed
# python/f1_api/openapi.json, both only reachable from root context.
#
# Pin base images by tag; record the digest here after the first real pull
# (`docker inspect --format='{{index .RepoDigests 0}}' <image>`):
#   node:22-slim  ->  sha256:<fill in after first pull>

FROM node:22-slim AS build

RUN corepack enable

WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile

COPY python/f1_api/openapi.json python/f1_api/openapi.json
COPY apps/web apps/web

RUN pnpm --filter @pitwall/web generate-client
RUN pnpm --filter @pitwall/web build

# `pnpm deploy` is pnpm's own "produce a self-contained, prod-only
# node_modules for one workspace package" command — this is what gives us
# just the production node_modules, per this prompt's own "copy only
# build/, package.json, and production node_modules" instruction. It packs
# by npm's own packlist rules (respects .gitignore, which excludes build/),
# so the built output is copied into the runtime stage separately below
# rather than relying on `deploy` to carry it along.
RUN pnpm --filter @pitwall/web deploy --prod /deploy

# ---------------------------------------------------------------------------
FROM node:22-slim AS runtime

RUN groupadd --system pitwall && useradd --system --gid pitwall --create-home pitwall

WORKDIR /app
COPY --from=build /deploy/package.json ./package.json
COPY --from=build /deploy/node_modules ./node_modules
COPY --from=build /repo/apps/web/build ./build

ARG GIT_SHA=dev
ARG BUILD_TIME=unknown
ENV PUBLIC_GIT_SHA=${GIT_SHA} \
    PUBLIC_BUILD_TIME=${BUILD_TIME} \
    NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0

USER pitwall
EXPOSE 3000
CMD ["node", "build/index.js"]
