# Production image of the static site (landing, pricing and the app). In dev,
# docker-compose mounts ./frontend into nginx instead, so edits show up live.
FROM nginx:alpine

COPY . /usr/share/nginx/html
RUN rm -f /usr/share/nginx/html/Dockerfile

EXPOSE 80
