FROM python:3.10.4-slim

RUN apt-get update
RUN apt-get install git -y

RUN wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
RUN apt-get install ./google-chrome-stable_current_amd64.deb

ENV NAME psybot
ENV APP_HOME /home/psybot

RUN groupadd -g 1000 -r ${NAME} && useradd -r -g ${NAME} -u 1000 ${NAME}

COPY requirements.txt requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

WORKDIR ${APP_HOME}

RUN chown ${NAME}:${NAME} ${APP_HOME}

USER ${NAME}

COPY --chown=${NAME}:${NAME} ./psybot/ ${APP_HOME}/psybot/
COPY --chown=${NAME}:${NAME} ./bot.py ${APP_HOME}/

CMD ["python", "-u", "bot.py"]
