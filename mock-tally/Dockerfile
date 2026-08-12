# Dev/test-only stand-in for a real Tally instance (see server.py for why).
# Not part of docker-compose.yml — only wired into docker-compose.dev.yml.
FROM python:3.12-alpine
WORKDIR /app
COPY server.py .
EXPOSE 9000
CMD ["python3", "server.py"]
