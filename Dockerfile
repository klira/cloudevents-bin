FROM python:3.6.5

RUN pip install pipenv

COPY Pipfile Pipfile.lock /usr/src/app/
WORKDIR /usr/src/app

RUN pipenv install --system --deploy

COPY db.py app.py /usr/src/app/

CMD ["python", "app.py"]
