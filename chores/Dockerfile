ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apk add --no-cache python3 py3-pip tzdata jq

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY app /app/app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 8099

CMD ["/run.sh"]
