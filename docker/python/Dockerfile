FROM python:3.10
LABEL maintainer="contact@illuin.tech"

RUN apt-get update -y && apt-get upgrade -y

WORKDIR /server_app/

RUN ln -snf /usr/share/zoneinfo/Europe/Paris /etc/localtime && echo Europe/Paris > /etc/timezone

RUN pip install gunicorn

COPY ./requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ./ /server_app/

CMD ["gunicorn", "--workers=4", "--bind=0.0.0.0:8000", "ubicast:app"]
