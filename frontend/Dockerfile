# 前端镜像 — 多阶段构建（Node 构建 + Nginx 托管）
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./

RUN npm ci --registry=https://registry.npmmirror.com

COPY . .

RUN npm run build

# --- 运行阶段：复用服务器上已有的 nginx:latest 镜像 ---
FROM nginx:latest

COPY --from=build /app/dist /usr/share/nginx/html

COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
