import typing
import datetime
import argparse
import threading
import collections.abc

from nntpserver import (
    NNTPServer,
    NNTPGroup,
    NNTPConnectionHandler,
    NNTPAuthSetting,
    NNTPPostSetting,
    NNTPArticleNotFound,
    Article,
    ArticleInfo,
)


EXAMPLE_ARTICLE = Article(
    ArticleInfo(
        1,
        "Hello world!",
        "epilys <epilys@example.com>",
        datetime.datetime.now(tz=datetime.timezone.utc),
        "<unique@example.com>",
        "",
        len("Hello from NNTP."),
        1,
        {},
    ),
    "Hello from NNTP.",
)


class Articles(NNTPGroup):
    _name: str = "example.all"

    def __init__(self, server: "ExampleNNTPServer") -> None:
        self.server: "ExampleNNTPServer" = server

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._name

    @property
    def number(self) -> int:
        return self.server.count

    @property
    def high(self) -> int:
        return self.server.high

    @property
    def low(self) -> int:
        return self.server.low

    @property
    def articles(self) -> typing.Dict[int, ArticleInfo]:
        return {1: EXAMPLE_ARTICLE.info}

    @property
    def created(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

    @property
    def posting_permitted(self) -> bool:
        return False


class ExampleNNTPServer(NNTPServer, collections.abc.Mapping):
    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        self.all: Articles = Articles(self)
        self._groups: typing.Dict[str, NNTPGroup] = {self.all.name: self.all}
        self.count: int = 1
        self.high: int = 1
        self.low: int = 1
        super().__init__(*args, **kwargs)

    def refresh(self) -> None:
        pass

    @property
    def groups(self) -> typing.Dict[str, NNTPGroup]:
        return self._groups

    @property
    def articles(self) -> typing.Dict[int, ArticleInfo]:
        return {1: EXAMPLE_ARTICLE.info}

    def __getitem__(self, key: typing.Union[str, int]) -> ArticleInfo:
        if isinstance(key, ArticleInfo):  # FIXME: is this a bug?
            return key
        if isinstance(key, str):
            try:
                key = int(key.strip())
            except ValueError:
                pass
        if isinstance(key, int):
            if key != 1:
                raise NNTPArticleNotFound(str(key))
        if (
            isinstance(key, str) and key.strip() != EXAMPLE_ARTICLE.info.message_id
        ) or key != 1:
            raise NNTPArticleNotFound(key)
        return EXAMPLE_ARTICLE.info

    def __iter__(self) -> typing.Iterator[typing.Any]:
        return iter([1])

    def __len__(self) -> int:
        return self.count

    def article(self, key: typing.Union[str, int]) -> Article:
        if isinstance(key, ArticleInfo):  # FIXME: is this a bug?
            return key
        if isinstance(key, str):
            try:
                key = int(key.strip())
            except ValueError:
                pass
        if isinstance(key, int):
            if key != 1:
                raise NNTPArticleNotFound(str(key))
        if (
            isinstance(key, str) and key.strip() != EXAMPLE_ARTICLE.info.message_id
        ) or key != 1:
            raise NNTPArticleNotFound(key)
        return EXAMPLE_ARTICLE

    @property
    def subscriptions(self) -> typing.Optional[typing.List[str]]:
        return [self.all.name]

    @property
    def debugging(self) -> bool:
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Example NNTP server")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--host", type=str, default="localhost")
    parser.add_argument("--use-ssl", action="store_true", default=False)
    parser.add_argument("--connect-with-nntplib", action="store_true", default=False)
    parser.add_argument("--certfile", type=str, default=None)
    parser.add_argument("--keyfile", type=str, default=None)

    args = parser.parse_args()
    host = args.host
    server_kwargs = {}
    if args.use_ssl:
        server_kwargs["use_ssl"] = True
        server_kwargs["certfile"] = args.certfile
        server_kwargs["keyfile"] = args.keyfile
    server_kwargs["auth"] = NNTPAuthSetting.NOAUTH
    server_kwargs["can_post"] = NNTPPostSetting.NOPOST

    ExampleNNTPServer.allow_reuse_address = True

    # Create the server, binding to localhost on port 9999
    with ExampleNNTPServer(
        (args.host, args.port), NNTPConnectionHandler, **server_kwargs
    ) as server:
        print(f"Listening on {args.host}:{args.port}")
        server.allow_reuse_address = True
        # Activate the server; this will keep running until you
        # interrupt the program with Ctrl-C
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        if args.connect_with_nntplib:
            import nntplib

            print("Connecting with nntplib...")
            if args.use_ssl:
                s = nntplib.NNTP_SSL(host=args.host, port=args.port)
            else:
                s = nntplib.NNTP(host=args.host, port=args.port)
            caps = s.getcapabilities()
            resp, count, first, last, name = s.group(Articles._name)
            resp, overviews = s.xover(1, 1)
            for artnum, over in overviews:
                author = over["from"]
                subject = over["subject"]
                lines = int(over[":lines"])
                print(f"{artnum} {author} {subject} ({lines})")
            print("Done with nntplib.")
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
