"""O REPL: lê linha, despacha, escreve. A única parte do sistema que toca em streams.

O ``Cli`` não conhece livro nem nível de preço, e o domínio não conhece este módulo. Entre
os dois passam apenas ``Command``, na ida, e ``Event``, na volta.
"""

from __future__ import annotations

from typing import Final, TextIO, assert_never

from matching_engine.domain.engine import MatchingEngine
from matching_engine.io.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    PrintBook,
    Quit,
    SubmitLimit,
    SubmitMarket,
    SubmitPegged,
)
from matching_engine.io.parser import parse
from matching_engine.io.presenter import format_events

PROMPT: Final = ">>> "


class Cli:
    """Fronteira entre o texto e a engine, para uma sessão.

    A engine chega pelo construtor porque é o que torna o ``Cli`` testável: cada teste
    monta a sua, e o estado de uma sessão não vaza para a seguinte. É injeção no sentido
    literal — um parâmetro —, sem contêiner, sem registro e sem fábrica, que é tudo
    abstração especulativa para um sistema com uma implementação só.
    """

    __slots__ = ("_engine", "_should_quit")

    def __init__(self, engine: MatchingEngine) -> None:
        self._engine = engine
        self._should_quit = False

    @property
    def should_quit(self) -> bool:
        """Se a sessão pediu para encerrar. É como o laço de ``run`` sabe parar.

        O sinal sai daqui, e não de uma comparação da linha crua com ``"quit"`` dentro do
        laço, porque a gramática mora no parser: reconhecer ``quit`` em dois lugares seria
        garantir que um dia ``exit`` fosse aceito num deles e não no outro.
        """
        return self._should_quit

    def execute(self, line: str) -> list[str]:
        """Uma linha de entrada vira as linhas de saída que ela produz.

        Erro de usuário vira uma linha ``Error:`` e a sessão continua. O que se captura é
        ``ValueError``, e isso é a taxonomia da seção 6 do contrato aplicada na fronteira:
        ``ParseError``, ``InvalidPriceError`` e ``InvalidOrderError`` derivam de
        ``ValueError`` justamente por descreverem entrada inválida, então a fronteira
        trata a família inteira sem enumerar exceção por exceção — e um erro de entrada
        novo, criado adiante no domínio, já chega tratado.

        Exceções derivadas de ``RuntimeError`` — ``QueueIntegrityError``,
        ``LevelIntegrityError``, ``BookIntegrityError`` — **não** são capturadas, de
        propósito. Elas significam que a engine perdeu a conta de onde suas ordens estão,
        e nesse ponto o livro já não é confiável: continuar a sessão seria seguir
        executando ordens contra um estado corrompido, imprimindo ``Error:`` como se
        fosse culpa de quem digitou. Estourar alto, com stack trace, é a resposta correta
        a bug interno.
        """
        try:
            command = parse(line)
            if command is None:
                return []
            return self._dispatch(command)
        except ValueError as error:
            return [f"Error: {error}"]

    def _dispatch(self, command: Command) -> list[str]:
        """Entrega o comando à engine e formata o que ela devolver.

        O ramo final com ``assert_never`` é o que faz o ``mypy`` recusar a build quando um
        comando novo aparece em ``commands.py`` sem tratamento aqui. Sem ele, o comando
        novo cairia no vazio e o REPL responderia com silêncio.
        """
        match command:
            case SubmitLimit(side=side, price=price, quantity=quantity):
                return format_events(self._engine.submit_limit(side, price, quantity))
            case SubmitMarket(side=side, quantity=quantity):
                return format_events(self._engine.submit_market(side, quantity))
            case SubmitPegged():
                return [_not_implemented("peg")]
            case CancelOrder():
                return [_not_implemented("cancel order")]
            case ModifyOrder():
                return [_not_implemented("modify order")]
            case PrintBook():
                return format_events(self._engine.snapshot())
            case Quit():
                self._should_quit = True
                return []
            case _:
                assert_never(command)


def run(cli: Cli, stream_in: TextIO, stream_out: TextIO) -> None:
    """Laço de leitura, parametrizado pelos streams.

    Os streams são parâmetros, e não ``sys.stdin`` e ``sys.stdout`` lidos aqui dentro,
    porque é isso que permite testar o REPL inteiro com ``StringIO`` — sem terminal, sem
    monkeypatch de módulo global e sem captura de saída de processo.

    A leitura é ``readline`` e não ``for line in stream_in``: iterar um arquivo usa um
    buffer de leitura antecipada, que numa sessão interativa engoliria o pedido de entrada
    seguinte e faria o prompt aparecer fora de hora.

    Encerra de três maneiras, e as três são saídas normais. Por ``quit``/``exit``; por EOF
    — Ctrl-D no Unix, Ctrl-Z seguido de Enter no Windows, ou fim do arquivo quando a
    entrada vem redirecionada —, caso em que ``readline`` devolve string vazia, que é
    distinta de ``"\\n"`` e por isso não se confunde com uma linha em branco; e por
    Ctrl+C. O EOF vale como saída porque um script canalizado para o REPL termina sem
    digitar ``quit``, e travar esperando entrada que não virá é pior do que sair.
    """
    try:
        while True:
            stream_out.write(PROMPT)
            stream_out.flush()

            line = stream_in.readline()
            if not line:
                # O prompt ficou sem resposta na tela; a quebra devolve o cursor ao shell.
                stream_out.write("\n")
                stream_out.flush()
                return

            for output in cli.execute(line):
                stream_out.write(f"{output}\n")
            stream_out.flush()

            if cli.should_quit:
                return
    except KeyboardInterrupt:
        # Ctrl+C num REPL é pedido de encerramento, não falha: sai como o EOF sai, com a
        # quebra que devolve o cursor ao shell. Deixar a exceção subir imprimiria um
        # traceback do interpretador sobre um pedido perfeitamente legítimo do usuário.
        stream_out.write("\n")
        stream_out.flush()


def _not_implemented(name: str) -> str:
    return f"Error: comando ainda não implementado: {name}"
