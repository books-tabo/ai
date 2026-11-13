# Isolated, disposable PayPal test service. Never use as a production image.
FROM postgres:17.11-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/tabo-test
ENV PATH="/opt/tabo-test/bin:${PATH}" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt && pip check
COPY --chown=postgres:postgres . .
ENV TABO_SANDBOX_CONTAINER=1 TABO_ENV=paypal-sandbox TABO_SITE_ENV=review PAYPAL_MODE=test PORT=10000
USER postgres
EXPOSE 10000
ENTRYPOINT ["python", "-m", "testing.paypal_sandbox"]

