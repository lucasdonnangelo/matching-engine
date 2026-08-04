import io

from matching_engine.domain.engine import MatchingEngine
from matching_engine.io.cli import PROMPT, Cli, run


class InterruptedInput(io.StringIO):
    """Entrada que recebe Ctrl+C: ``readline`` levanta onde o terminal levantaria.

    Num terminal, o SIGINT chega durante a leitura bloqueante e vira ``KeyboardInterrupt``
    de dentro do ``readline``. A classe reproduz esse ponto exato, depois de deixar passar
    as linhas que o usuário já tinha digitado.
    """

    def __init__(self, text: str, interrupt_after: int) -> None:
        super().__init__(text)
        self._remaining = interrupt_after

    def readline(self, size: int | None = -1) -> str:
        if self._remaining == 0:
            raise KeyboardInterrupt
        self._remaining -= 1
        return super().readline()


def test_eof_on_an_empty_session_ends_cleanly() -> None:
    """Entrada exaurida encerra sem exceção; sobra o prompt sem resposta e a quebra."""
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), io.StringIO(""), stream_out)

    assert stream_out.getvalue() == f"{PROMPT}\n"


def test_eof_after_a_command_ends_cleanly() -> None:
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), io.StringIO("limit buy 10 100\n"), stream_out)

    assert stream_out.getvalue() == f"{PROMPT}Order created: buy 100 @ 10 1\n{PROMPT}\n"


def test_ctrl_c_ends_like_eof_ends() -> None:
    """Ctrl+C é encerramento normal: mesma saída do EOF, sem traceback subindo."""
    stream_in = InterruptedInput("limit buy 10 100\n", interrupt_after=1)
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), stream_in, stream_out)

    assert stream_out.getvalue() == f"{PROMPT}Order created: buy 100 @ 10 1\n{PROMPT}\n"


def test_ctrl_c_before_typing_anything_ends_cleanly() -> None:
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), InterruptedInput("", interrupt_after=0), stream_out)

    assert stream_out.getvalue() == f"{PROMPT}\n"
