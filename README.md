# Matching Engine

Motor de casamento de ordens para ativo único, com prioridade preço-tempo, suportando ordens
limit, market e pegged, além de cancelamento e alteração de ordens.

> **Em construção.** A arquitetura, as complexidades algorítmicas e as decisões de projeto ficarão
> documentadas neste próprio README e em [`docs/adr/`](docs/adr/).

## Requisitos

- Python 3.12+

## Setup

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Testes

```bash
pytest          # suíte de testes
ruff check .    # lint
mypy            # verificação de tipos
```
