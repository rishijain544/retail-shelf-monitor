# syntax=docker/dockerfile:1

# ------------------------------------------------------------------ deps
FROM node:20-alpine AS deps
WORKDIR /app
# `npm ci` (not `npm install`) so the image matches package-lock.json exactly.
COPY package.json package-lock.json ./
RUN npm ci

# ------------------------------------------------------------------ dev
# The old behaviour, kept reachable: `target: dev` in docker-compose.yml plus a
# 5173:5173 port mapping gives back the hot-reloading Vite server.
FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]

# ------------------------------------------------------------------ build
FROM deps AS build
# Vite inlines import.meta.env.* into the bundle at build time, so the API URL
# has to arrive as a build argument. Setting it as a runtime environment
# variable on the container does nothing at all — the value is already baked in.
ARG VITE_API_URL=http://localhost:8000/api/v1
ENV VITE_API_URL=$VITE_API_URL
COPY . .
RUN npm run build

# ------------------------------------------------------------------ runtime
FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf

# The whole dist/ tree, not index.html plus a hand-picked bundle. The Trends
# route is React.lazy'd, so the build emits several hashed chunks under
# dist/assets/ and every one of them has to ship — copying the directory keeps
# that true as the chunk list changes, where naming files individually would
# silently drop the lazy chunk and 404 on /trends.
COPY --from=build /app/dist/ /usr/share/nginx/html/

EXPOSE 80
