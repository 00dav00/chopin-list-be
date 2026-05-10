.PHONY: install test all

install:
	pip install -r requirements.txt -r requirements-dev.txt

test:
	pytest

all: install test
