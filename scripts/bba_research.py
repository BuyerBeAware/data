
name: BBA Project Research

on:
  workflow_dispatch:
  schedule:
    - cron: '0 12 * * *'

permissions:
  contents: read

jobs:
  research:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install OpenAI SDK
        run: |
          python -m pip install --upgrade pip
          python -m pip install openai

      - name: Validate project data
        run: |
          python -m json.tool projects.json > /dev/null

      - name: Run BBA AI research
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          python scripts/bba_research.py

      - name: Upload research candidates
        uses: actions/upload-artifact@v4
        with:
          name: bba-research-candidates
          path: research_candidates.json
          retention-days: 14
