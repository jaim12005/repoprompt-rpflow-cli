.PHONY: test doctor demo

test:
	python3 -m py_compile src/rpflow/*.py

doctor:
	python3 -m rpflow doctor

demo:
	python3 -m rpflow exec --workspace GitHub --tab T1 -e 'tabs'
