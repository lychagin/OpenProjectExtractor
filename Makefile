.PHONY: install run test check clean

install:
	pip install -r requirements.txt

run:
	python -m src.extractor

test:
	python -m pytest tests/ -v

check:
	@echo "Checking output directory..."
	@test -d output && echo "Output directory exists" || mkdir -p output
	@echo "Checking for latest CSV file..."
	@latest=$$(ls -t output/res-*.csv 2>/dev/null | head -1) && \
		if [ -z "$$latest" ]; then \
			echo "No CSV files found in output/"; \
			exit 1; \
		else \
			echo "Latest output: $$latest"; \
			lines=$$(wc -l < "$$latest"); \
			echo "Lines in file: $$lines"; \
			if [ "$$lines" -lt 2 ]; then \
				echo "ERROR: CSV file has no data rows"; \
				exit 1; \
			else \
				echo "Validation passed: $$lines lines (including header)"; \
			fi \
		fi

clean:
	rm -rf output/res-*.csv
	@echo "Cleaned output files"
