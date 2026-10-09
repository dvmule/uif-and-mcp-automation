# Multi-stage build → one small, non-root runtime image (the build/deploy contract).
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
COPY openapi.yaml ./
RUN npm run build

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/openapi.yaml ./openapi.yaml
COPY package.json ./
# The node image's built-in non-root user, by its NUMERIC id (hadolint
# DL3066): a name resolves inside the image, but Kubernetes runAsNonRoot and
# host tooling need the number. 1000 is the node user's fixed uid upstream.
USER 1000
EXPOSE 8080
CMD ["node", "dist/server.js"]
