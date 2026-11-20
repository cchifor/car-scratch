# syntax=docker/dockerfile:1.7-labs
# Builds the Car Scratch editor (scratch-gui playground) as static files.
#   docker buildx build -f docker/editor.Dockerfile --target www --output type=local,dest=out/www .
FROM node:24.22.3-trixie AS deps
ENV HUSKY=0 CI=true NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /src
# Manifests first so dependency install is cached across source edits.
COPY --parents editor/package.json editor/package-lock.json editor/packages/*/package.json editor/packages/scratch-gui/scripts ./
WORKDIR /src/editor
RUN --mount=type=cache,target=/root/.npm npm ci

FROM deps AS src
COPY editor/ /src/editor/
# Build the sibling packages in workspace order (as the root `npm run build` does): the GUI bundle uses
# their sources via the "webpack" export condition, but TypeScript and some imports resolve their dist/.
RUN for p in task-herder scratch-storage scratch-svg-renderer scratch-paint scratch-render scratch-vm; do       npm run build --workspace=packages/$p || exit 1; done

FROM src AS build
WORKDIR /src/editor/packages/scratch-gui
ENV NODE_OPTIONS=--max-old-space-size=8192
RUN NODE_ENV=production npx webpack \
 && cd build && find . -name '*.map' -delete \
 && find . -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' -o -name '*.json' -o -name '*.webmanifest' -o -name '*.wasm' \) -size +1k -exec gzip -9 -k {} \;

FROM src AS test
WORKDIR /src/editor

FROM scratch AS www
COPY --from=build /src/editor/packages/scratch-gui/build/ /
