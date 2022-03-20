import typing
import datetime
import argparse
import threading
import collections.abc
import http.client
import json
import re
import sqlite3


from nntpserver import (
    NNTPServer,
    NNTPGroup,
    NNTPConnectionHandler,
    NNTPAuthSetting,
    NNTPPostSetting,
    NNTPArticleNotFound,
    NNTPServerError,
    Article,
    ArticleInfo,
)

MSG_ID_RE = re.compile(r"<(?P<id>\d+)@news.ycombinator.com>")


class Articles(NNTPGroup):
    _name: str = "hn.all"

    def __init__(self, server: "HNNNTPServer") -> None:
        self.server: "HNNNTPServer" = server

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
    def articles(self) -> typing.Dict[typing.Union[int, str], ArticleInfo]:
        return self.server

    @property
    def created(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

    @property
    def posting_permitted(self) -> bool:
        return False


def get_to_json(url) -> typing.Any:
    conn = http.client.HTTPSConnection("hacker-news.firebaseio.com")
    conn.request("GET", url)
    r1 = conn.getresponse()
    if r1.status != 200:
        raise NNTPServerError(
            f"Could not connect to HN: API returned {r1.status} {r1.reason}"
        )
    try:
        data = r1.read().decode("utf-8")
        data = json.loads(data)
        return data
    except Exception as exc:
        raise NNTPServerError(f"Read invalid data from HN: {exc}")


class HNNNTPServer(NNTPServer, collections.abc.Mapping):
    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        self.all: Articles = Articles(self)
        self._groups: typing.Dict[str, NNTPGroup] = {self.all.name: self.all}
        self.count: int = 0
        self.high: int = 0
        self.low: int = 0
        self.article_index: typing.Dict[int, typing.Optional[Article]] = {}
        self.build_index()
        super().__init__(*args, **kwargs)

    def row_to_article(self, story) -> None:
        i = story["id"]
        body = story["body"]
        info = ArticleInfo(
            i,
            story["title"],
            f"{story['by']}@news.ycombinator.com",
            datetime.datetime.fromtimestamp(story["time"], tz=datetime.timezone.utc),
            f"<{i}@news.ycombinator.com>",
            f"<{story['parent']}@news.ycombinator.com>" if "parent" in story else "",
            len(body),
            len(body.split()),
            {"Permalink": f"https://news.ycombinator.com/item?id={i}"},
        )

        self.article_index[i] = Article(info, body)
        return

    def get_conn(self):
        conn = sqlite3.connect("hn_cache.db", isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE IF NOT EXISTS articles(
        id INTEGER PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        by TEXT NOT NULL,
        time INTEGER NOT NULL,
        kids TEXT NOT NULL CHECK(json_valid(kids)),
        parent INTEGER,
        body TEXT NOT NULL
        );"""
        )
        return conn

    def build_index(self):
        self.count: int = 0
        self.high: int = 0
        self.low: int = 0
        self.article_index: typing.Dict[int, typing.Optional[Article]] = {}
        conn = self.get_conn()
        cur = conn.cursor()
        for row in cur.execute("SELECT * FROM articles ORDER BY id"):
            self.row_to_article(row)

        self.refresh()

        self.count = len(self.article_index)
        if self.count == 0:
            self.high = 0
            self.low = 0
            return
        print("count", self.count)
        self.high = max(self.article_index.keys())
        self.low = min(self.article_index.keys())

    def refresh(self) -> None:
        data = get_to_json("/v0/topstories.json")
        for i in data[:40]:
            if i not in self.article_index:
                self.high = max(self.high, i)
                self.low = min(self.low, i)
                self.count += 1
                self.article_index[i] = None

    @property
    def groups(self) -> typing.Dict[str, NNTPGroup]:
        return self._groups

    @property
    def articles(self) -> typing.Dict[typing.Union[int, str], ArticleInfo]:
        return self

    def newnews(
        self, wildmat: str, date: datetime.datetime
    ) -> typing.Optional[typing.Iterator[ArticleInfo]]:
        conn = self.get_conn()
        cur = conn.cursor()
        for row in cur.execute(
            "SELECT id FROM articles WHERE time > ? ORDER BY id", (date.timestamp())
        ):
            yield self[row["id"]]
        return None

    def warm(self, i) -> Article:
        if self.article_index[i]:
            return typing.cast(Article, self.article_index[i])
        print(f"Getting story {i}")

        story = get_to_json(f"/v0/item/{i}.json?print=pretty")
        print(f"for {i} I got ", story)
        if "text" in story:
            body = story["text"]
        elif "url" in story:
            body = story["url"]
        else:
            body = ""
        info = ArticleInfo(
            i,
            story["title"] if "title" in story else "",
            f"{story['by']}@news.ycombinator.com",
            datetime.datetime.fromtimestamp(story["time"], tz=datetime.timezone.utc),
            f"<{i}@news.ycombinator.com>",
            f"<{story['parent']}@news.ycombinator.com>" if "parent" in story else "",
            len(body),
            len(body.split()),
            {"Permalink": f"https://news.ycombinator.com/item?id={i}"},
        )
        self.article_index[i] = Article(info, body)
        print(f"Got {self.article_index[i]}")
        conn = self.get_conn()
        cursor = conn.cursor()
        print("INSERTING : ")
        cursor.execute(
            """INSERT OR IGNORE INTO articles(id, title, by, time,parent, kids, body) VALUES
    (?, ?, ?, ?,?, ?, ?)""",
            (
                i,
                info.subject,
                story["by"],
                story["time"],
                story["parent"] if "parent" in story else None,
                json.dumps(story["kids"]) if "kids" in story else "[]",
                body,
            ),
        )
        conn.commit()
        conn.close()
        return typing.cast(Article, self.article_index[i])

    def __getitem__(self, key: typing.Union[str, int]) -> ArticleInfo:
        if isinstance(key, str):
            try:
                key = int(key.strip())
            except ValueError:
                pass
        if isinstance(key, str):
            try:
                key = int(MSG_ID_RE.match(key).groups("id")[0])
                return self.warm(key).info
            except (KeyError, AttributeError, ValueError):
                raise NNTPArticleNotFound(key)
        try:
            if key not in self.article_index:
                raise NNTPArticleNotFound(key)
            return self.warm(key).info
        except KeyError:
            raise NNTPArticleNotFound(key)

    def __iter__(self) -> typing.Iterator[typing.Union[str, int]]:
        return (k for k in self.article_index)

    def __len__(self) -> int:
        return self.count

    def article(self, key: typing.Union[str, int]) -> Article:
        if isinstance(key, str):
            try:
                key = int(key.strip())
            except ValueError:
                pass
        if isinstance(key, str):
            try:
                key = int(MSG_ID_RE.match(key).groups("id")[0])
                return self.warm(key)
            except (KeyError, AttributeError, ValueError):
                raise NNTPArticleNotFound(key)
        try:
            if key not in self.article_index:
                raise NNTPArticleNotFound(key)
            return self.warm(key)
        except KeyError:
            raise NNTPArticleNotFound(key)

    @property
    def subscriptions(self) -> typing.Optional[typing.List[str]]:
        return [self.all.name]

    @property
    def debugging(self) -> bool:
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HN NNTP server")
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

    HNNNTPServer.allow_reuse_address = True

    # Create the server, binding to localhost on port 9999
    with HNNNTPServer(
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

            s: typing.Union[nntplib.NNTP_SSL, nntplib.NNTP]
            print("Connecting with nntplib...")
            if args.use_ssl:
                s = nntplib.NNTP_SSL(host=args.host, port=args.port)
            else:
                s = nntplib.NNTP(host=args.host, port=args.port)
            caps = s.getcapabilities()
            resp, count, first, last, name = s.group(Articles._name)
            resp, overviews = s.xover(first, last)
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
