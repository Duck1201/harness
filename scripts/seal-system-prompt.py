#!/usr/bin/env python
"""Reseals the derived half of SYSTEM-PROMPT.md from the contracts.

The Operator edits the block below the marker; everything above it is a mirror
of what ``build_system_prompt`` produces, kept in the repository so the exact
text the model receives can be read without opening the Python. The mirror
carries ``{{TODAY}}`` instead of a date on purpose: a real date would be false
tomorrow and the staleness test would fail on its own every morning.

    uv run python scripts/seal-system-prompt.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness import load_config  # noqa: E402
from harness.system_prompt import OPERATOR_MARKER, derived_prompt_mirror  # noqa: E402

TARGET = ROOT / "SYSTEM-PROMPT.md"

# A marca aparece uma única vez no arquivo, na última linha do cabeçalho: o leitor
# corta na primeira ocorrência, então citá-la na prosa faria o texto acima dela
# virar bloco do Operator.
_HEADER = """# System prompt

Este cabeçalho inteiro, até a marca HTML da última linha, é gerado por
`uv run python scripts/seal-system-prompt.py` a partir de
`src/harness/system_prompt.py` e das capacidades declaradas em
`config/model-profiles.json`. Editar aqui não muda o prompt; para mudar o texto
derivado, mude a função ou o contrato e rode o comando de novo.

`{{{{TODAY}}}}` é substituído pela data do host a cada Turn — produção passa o
relógio em UTC, o corpus passa `BENCH_DATE`.

```text
{mirror}
```

Abaixo da marca vai o texto do Operator, anexado ao fim do prompt — escreva ali.
Bloco vazio significa que o prompt é exatamente o de cima, byte a byte. Ele entra
em todo Turn e é limitado a 4000 caracteres.

{marker}
"""


def main() -> None:
    header = _HEADER.format(marker=OPERATOR_MARKER, mirror=derived_prompt_mirror(load_config()))
    # O bloco do Operator é preservado verbatim: o selador cuida da metade
    # derivada e nunca reescreve o que o Operator escreveu.
    notes = ""
    if TARGET.is_file():
        _, separator, existing = TARGET.read_text(encoding="utf-8").partition(OPERATOR_MARKER)
        notes = existing if separator else ""
    TARGET.write_text(header + notes.lstrip("\n"), encoding="utf-8")
    print(f"selado: {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
