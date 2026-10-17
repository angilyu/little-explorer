# Milo, the voice agent, for LiveKit Cloud (`lk agent deploy` from server/).
# Secrets (OPENAI_API_KEY, PIP_*) are set with `lk agent secrets`; LiveKit injects its own credentials.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 milo
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
# Bundle the books as a fallback when the content server is unreachable.
COPY content ./content
ENV CONTENT_DIR=/app/content/Books

RUN chown -R milo /app
USER milo
RUN python -m little_explorer_agent.agent download-files

CMD ["python", "-m", "little_explorer_agent.agent", "start"]
