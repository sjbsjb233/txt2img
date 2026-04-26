# syntax=docker/dockerfile:1.7

FROM node:20-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM deps AS builder-dev
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}
COPY . .
RUN pnpm build --mode development --sourcemap

FROM deps AS builder-prod
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}
COPY . .
RUN pnpm build

FROM nginx:alpine AS dev
COPY --from=builder-dev /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

FROM nginx:alpine AS prod
COPY --from=builder-prod /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
