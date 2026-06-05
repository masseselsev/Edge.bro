# Build stage
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine
COPY --from=build /app/dist /opt/new_dist
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN echo -e '#!/bin/sh\nrm -rf /usr/share/nginx/html/*\ncp -R /opt/new_dist/* /usr/share/nginx/html/\nexec "$@"' > /entrypoint.sh && chmod +x /entrypoint.sh
EXPOSE 7777
ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
