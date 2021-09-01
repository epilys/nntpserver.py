import abc
import socketserver
import typing
import datetime
import itertools
import enum
import re


class NNTPAuthSetting(enum.Flag):
    NOAUTH = 0
    SECUREONLY = enum.auto()
    REQUIRED = enum.auto()


class NNTPServerError(Exception):
    DEFAULT = "Server error"

    def __init__(self, *args: typing.Any) -> None:
        Exception.__init__(self, *args)
        try:
            self.response = args[0]
        except IndexError:
            self.response = self.DEFAULT


class NNTPAuthenticationError(NNTPServerError):
    DEFAULT = "Authentication error"


class NNTPPostSetting(enum.Flag):
    NOPOST = 0
    POST = enum.auto()
    AUTHREQUIRED = enum.auto()


class NNTPPostError(Exception):
    def __init__(self, *args: typing.Any) -> None:
        Exception.__init__(self, *args)
        try:
            self.response = args[0]
        except IndexError:
            self.response = "Post error"


class NNTPDataError(NNTPServerError):
    DEFAULT = "Data error"


class NNTPArticleNotFound(NNTPServerError):
    DEFAULT = "No article with that number"


try:
    import ssl
except ImportError:
    _have_ssl = False
else:
    _have_ssl = True

# from email.header import decode_header as _email_decode_header
import email.utils

# Standard port used by NNTP servers
NNTP_PORT = 119
NNTP_SSL_PORT = 563
_MAXLINE = 2048

_CRLF = b"\r\n"

# Default decoded value for LIST OVERVIEW.FMT if not supported
_DEFAULT_OVERVIEW_FMT = [
    "Subject:",
    "From:",
    "Date:",
    "Message-ID:",
    "References:",
    "Bytes:",
    "Lines:",
]


def parse_datetime(
    date_str: str, time_str: typing.Optional[str] = None
) -> datetime.datetime:
    """Parse a pair of (date, time) strings, and return a datetime object.
    If only the date is given, it is assumed to be date and time
    concatenated together (e.g. response to the DATE command).
    """
    if time_str is None:
        time_str = date_str[-6:]
        date_str = date_str[:-6]
    hours = int(time_str[:2])
    minutes = int(time_str[2:4])
    seconds = int(time_str[4:])
    year = int(date_str[:-4])
    month = int(date_str[-4:-2])
    day = int(date_str[-2:])
    # RFC 3977 doesn't say how to interpret 2-char years.  Assume that
    # there are no dates before 1970 on Usenet.
    if year < 70:
        year += 2000
    elif year < 100:
        year += 1900
    return datetime.datetime(
        year, month, day, hours, minutes, seconds, tzinfo=datetime.timezone.utc
    )


def format_datetime(
    dt: typing.Union[datetime.datetime, datetime.date], legacy: bool = False
) -> typing.Tuple[str, str]:
    """Format a date or datetime object as a pair of (date, time) strings
    in the format required by the NEWNEWS and NEWGROUPS commands.  If a
    date object is passed, the time is assumed to be midnight (00h00).

    The returned representation depends on the legacy flag:
    * if legacy is False (the default):
      date has the YYYYMMDD format and time the HHMMSS format
    * if legacy is True:
      date has the YYMMDD format and time the HHMMSS format.
    RFC 3977 compliant servers should understand both formats; therefore,
    legacy is only needed when talking to old servers.
    """
    if not isinstance(dt, datetime.datetime):
        time_str = "000000"
    else:
        time_str = "{0.hour:02d}{0.minute:02d}{0.second:02d}".format(dt)
    y = dt.year
    if legacy:
        y = y % 100
        date_str = "{0:02d}{1.month:02d}{1.day:02d}".format(y, dt)
    else:
        date_str = "{0:04d}{1.month:02d}{1.day:02d}".format(y, dt)
    return date_str, time_str


def parse_range(s) -> typing.Optional[typing.Tuple[int, typing.Optional[int]]]:
    s = s.strip()
    try:
        num = int(s)
        return (num, num)
    except:
        pass
    try:
        a, *bs = map(int, filter(bool, s.split("-")))
        if len(bs) == 0:
            b = None
        elif len(bs) == 1:
            b = bs[0]
        else:
            return None
        return (a, b)
    except:
        return None


class ArticleInfo(typing.NamedTuple):
    number: int
    subject: str
    from_: str
    date: datetime.datetime
    message_id: str
    references: str
    bytes: int
    lines: int
    headers: typing.Dict[str, str]

    def __str__(self) -> str:
        return "\t".join(
            [
                str(self.number),
                self.subject,
                self.from_,
                email.utils.format_datetime(self.date),
                self.message_id,
                self.references,
                str(self.bytes),
                str(self.lines),
            ]
            + [f"{k}: {v}" for k, v in self.headers.items()]
        )


class Article(typing.NamedTuple):
    info: ArticleInfo
    body: str


class NNTPGroup(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        ...

    @property
    @abc.abstractmethod
    def short_description(self) -> str:
        ...

    @property
    @abc.abstractmethod
    def number(self) -> int:
        ...

    @property
    @abc.abstractmethod
    def low(self) -> int:
        ...

    @property
    @abc.abstractmethod
    def high(self) -> int:
        ...

    @property
    @abc.abstractmethod
    def articles(self) -> typing.Dict[typing.Union[int, str], ArticleInfo]:
        ...

    @property
    @abc.abstractmethod
    def created(self) -> datetime.datetime:
        ...

    @property
    @abc.abstractmethod
    def posting_permitted(self) -> bool:
        ...


class NNTPServer(abc.ABC, socketserver.ThreadingMixIn, socketserver.TCPServer):
    overview_format: typing.List[str] = _DEFAULT_OVERVIEW_FMT

    def __init__(
        self,
        *args: typing.Any,
        auth: NNTPAuthSetting = NNTPAuthSetting.NOAUTH,
        can_post: NNTPPostSetting = NNTPPostSetting.NOPOST,
        use_ssl: bool = False,
        certfile: typing.Optional[str] = None,
        keyfile: typing.Optional[str] = None,
        **kwargs: typing.Any,
    ) -> None:
        self.auth = auth
        self.certfile = certfile
        self.keyfile = keyfile
        self.ssl_version = None
        self.can_post = can_post
        if use_ssl:
            if not certfile or not keyfile:
                raise ValueError(
                    "You must provide certfile and keyfile keyword arguments in NNTPServer.__init__ when use_ssl is True."
                )
            if not _have_ssl:
                raise ValueError(
                    "You set use_ssl to True but the ssl module could not be imported."
                )
            self.ssl_version = ssl_version = ssl.PROTOCOL_TLS
        super().__init__(*args, **kwargs)

    def get_request(self) -> typing.Tuple[typing.Any, typing.Tuple[str, int]]:
        if self.ssl_version:
            newsocket, fromaddr = self.socket.accept()
            connstream = ssl.wrap_socket(
                newsocket,
                server_side=True,
                certfile=self.certfile,
                keyfile=self.keyfile,
                ssl_version=self.ssl_version,
            )
            return connstream, fromaddr
        return super().get_request()

    @abc.abstractmethod
    def refresh(self) -> None:
        """Hook for refreshing internal state before processing article/group commands"""
        ...

    @property
    @abc.abstractmethod
    def groups(self) -> typing.Dict[str, NNTPGroup]:
        ...

    @property
    @abc.abstractmethod
    def articles(self) -> typing.Dict[typing.Union[int, str], ArticleInfo]:
        ...

    @abc.abstractmethod
    def article(self, key: typing.Union[str, int]) -> Article:
        ...

    def date(self) -> datetime.datetime:
        return datetime.datetime.utcnow()

    def newnews(
        self, wildmat: str, date: datetime.datetime
    ) -> typing.Optional[typing.List[ArticleInfo]]:
        return None

    def newgroups(
        self, date: datetime.datetime
    ) -> typing.Optional[typing.List[NNTPGroup]]:
        return None

    @property
    def debugging(self) -> bool:
        return False

    def auth_user(self, user: str, password: str) -> bytes:
        """Return an auth token on success. This token will be passed to the server for authenticated actions like POSTing articles."""
        raise NNTPAuthenticationError("Authentication not supported")

    def post(self, auth_token: typing.Optional[bytes], lines: str) -> None:
        raise NNTPPostError("Posting not supported")

    @property
    def subscriptions(self) -> typing.Optional[typing.List[str]]:
        return None

    @property
    def help(self) -> typing.Optional[str]:
        """Return an informational help text to the user. If None, a generic message will be generated."""
        return None


class NNTPConnectionHandler(socketserver.BaseRequestHandler):
    """
    The request handler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    server: NNTPServer

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        print("New connection.")
        # self.command_queue = collections.deque()
        self.command_history: typing.List[str] = []
        self._init: bool = True
        self._quit: bool = False
        self._authed: bool = False
        self._auth_token: typing.Optional[bytes] = None
        self._authed_user: typing.Optional[str] = None
        self._buffer: bytes = b""
        self.current_selected_newsgroup: typing.Optional[str] = None
        self.current_article_number: typing.Optional[int] = None
        super().__init__(*args, **kwargs)

    def handle(self) -> None:
        if self._quit:
            raise Exception("QUIT??")
        if self._init:
            if self.server.can_post:
                self.send_lines(["200 NNTP Service Ready, posting allowed"])
            else:
                self.send_lines(["201 NNTP Service Ready, posting prohibited"])
            self._init = False
        # self.request is the TCP socket connected to the client
        while True:
            try:
                self.data = self._getline()
            except NNTPDataError as exc:
                print(f"Data error: {exc}")
                self._quit = True
                self.send_lines(["205 Connection closing"])
                return
            data_caseless = self.data.casefold()
            if not self.data:
                continue
            if self.server.debugging and not data_caseless.startswith("authinfo"):
                print("got:", self.data)
            if data_caseless == "capabilities":
                self.capabilities()
            elif data_caseless.startswith("authinfo"):
                self.auth()
            elif data_caseless == "post":
                allow = False
                if self.server.can_post and not (
                    self.server.can_post & NNTPPostSetting.AUTHREQUIRED
                ):
                    allow = True
                elif self._authed and (
                    self.server.can_post & NNTPPostSetting.AUTHREQUIRED
                ):
                    allow = True
                elif not self._authed or not self.server.can_post:
                    pass
                elif not self._authed and (
                    self.server.can_post & NNTPPostSetting.AUTHREQUIRED
                ):
                    pass
                if not allow:
                    self.send_lines(["440 Posting not permitted"])
                else:
                    self.send_lines(["340 Input article; end with <CR-LF>.<CR-LF>"])
                    try:
                        lines = self._getlines()
                        self.server.post(self._auth_token, lines)
                        self.send_lines(["240 Article received OK"])
                    except NNTPDataError as exc:
                        print(f"Data error: {exc}")
                        self._quit = True
                        self.send_lines(["205 Connection closing"])
                        return
                    except NNTPPostError as exc:
                        self.send_lines([f"441 Posting failed: {exc.response}"])
            elif data_caseless.startswith("group"):
                _, group_name = self.data.split()
                self.select_group(group_name)
            elif data_caseless.startswith("over") or data_caseless.startswith("xover"):
                self.overview()
            elif data_caseless.startswith("hdr") or data_caseless.startswith("xhdr"):
                self.hdr()
            elif data_caseless.startswith("stat"):
                self.stat()
            elif data_caseless.startswith("article"):
                self.article()
            elif data_caseless.startswith("body"):
                self.article(body=True)
            elif data_caseless.startswith("head"):
                self.head()
            elif data_caseless == "help":
                self.help()
            elif data_caseless.startswith("listgroup"):
                self.listgroup()
            elif (
                data_caseless == "list newsgroups"
                or data_caseless == "list"
                or data_caseless.startswith("list active")
            ):
                self.list()
            elif data_caseless == "list subscriptions":
                subs: typing.Optional[typing.List[str]] = self.server.subscriptions
                if subs is None:
                    self.send_lines(["503 No list of recommended newsgroups available"])
                else:
                    self.send_lines(
                        ["215 List of recommended newsgroups follows"] + subs + ["."]
                    )
            elif data_caseless == "mode reader":
                if self.server.can_post:
                    self.send_lines(["200 NNTP Service Ready, posting allowed"])
                else:
                    self.send_lines(["201 NNTP Service Ready, posting prohibited"])
            elif data_caseless == "list overview.fmt":
                self.send_lines(
                    ["215 Order of fields in overview database."]
                    + self.server.overview_format
                    + ["."]
                )
            elif data_caseless == "date":
                date = self.server.date()
                self.send_lines([f"111 {''.join(format_datetime(date))}"])
            elif data_caseless.startswith("newnews"):
                self.newnews()
            elif data_caseless.startswith("newgroups"):
                self.newgroups()
            elif data_caseless == "quit":
                self._quit = True
                self.send_lines(["205 Connection closing"])
                return
            else:
                self.send_lines(["500 Unknown command"])
                continue
            self.command_history.append(self.data)

    AUTHINFO_RE = re.compile(
        r"^authinfo\s*(?P<keyword>(?:pass)|(?:user))\s*(?P<value>.*)$",
        re.IGNORECASE,
    )

    def auth(self) -> None:
        self.server.refresh()
        # Don't use .split() because password may contain white spaces
        match = self.AUTHINFO_RE.search(self.data.strip())
        if not match:
            self.send_lines(["501 Syntax Error"])
            return
        keyword = match.group("keyword").casefold()
        value = match.group("value")
        if not self.server.auth or self._authed:
            self.send_lines(["502 Command unavailable"])
            return
        if keyword == "user":
            self._authed_user = value
            self.send_lines(["381 Enter passphrase"])
            return

        if not self._authed_user:
            self.send_lines(["482 Authentication commands issued out of sequence"])
            return

        try:
            self._auth_token = self.server.auth_user(self._authed_user, value)
            self.send_lines([f"281 Authentication accepted"])
            self._authed = True
        except NNTPAuthenticationError as exc:
            self.send_lines([f"481 {exc.response}"])

    def capabilities(self) -> None:
        show_auth = False
        if not self._authed and self.server.auth:
            if (
                self.server.auth & NNTPAuthSetting.SECUREONLY
            ) and not self.server.ssl_version:
                pass
            else:
                show_auth = True

        capabilities = [
            "101 Capability list:",
            "VERSION 2",
            "READER",
            "HDR",
            "NEWNEWS",
            "LIST ACTIVE NEWSGROUPS OVERVIEW.FMT SUBSCRIPTIONS",
            "OVER MSGID",
        ]
        if self.server.can_post:
            capabilities.append("POST")
        if show_auth:
            capabilities.append("AUTHINFO USER")
        capabilities.append(".")
        self.send_lines(capabilities)

    def newnews(self) -> None:
        self.server.refresh()
        command, *tokens = self.data.strip().split()
        if len(tokens) < 2:
            self.send_lines(["501 Syntax Error"])
            return
        wildmat, date_str, time_str, *gmt = tokens
        try:
            date = parse_datetime(date_str, time_str=time_str)
        except TypeError:
            self.send_lines(["501 Syntax Error"])
            return
        # Check if server implements newnews, otherwise compute newnews on our own.
        articles = self.server.newnews(wildmat, date)
        if articles is None:
            articles = list(
                filter(
                    lambda a: a.date >= date,
                    itertools.chain.from_iterable(
                        g.articles.values()
                        for g in filter(
                            lambda g: g.name == wildmat, self.server.groups.values()
                        )
                    ),
                )
            )
        self.send_lines(
            ["230 list of new articles by message-id follows"]
            + list(article.message_id for article in articles)
            + ["."]
        )

    def newgroups(self) -> None:
        self.server.refresh()
        command, *tokens = self.data.strip().split()
        if len(tokens) < 2:
            self.send_lines(["501 Syntax Error"])
            return
        date_str, time_str, *gmt = tokens
        try:
            date = parse_datetime(date_str, time_str=time_str)
        except TypeError:
            self.send_lines(["501 Syntax Error"])
            return
        # Check if server implements newgroups, otherwise compute newgroups on our own.
        groups = self.server.newgroups(date)

        if groups is not None:
            groups = typing.cast(typing.List[NNTPGroup], groups)
        else:
            groups = list(
                filter(lambda g: g.created >= date, self.server.groups.values())
            )

        self.send_lines(
            ["231 list of new newsgroups follows"]
            + [f"{g.name} {g.high} {g.low} n" for g in groups]
            + ["."]
        )

    def listgroup(self) -> None:
        self.server.refresh()
        command, *tokens = self.data.strip().split()
        if len(tokens) == 0 and self.current_selected_newsgroup is None:
            self.send_lines(["412 No newsgroups selected"])
            return
        group_name = tokens[0]
        if group_name != self.current_selected_newsgroup:
            if not self.select_group(group_name):
                return
        group = self.server.groups[typing.cast(str, self.current_selected_newsgroup)]
        if len(tokens) > 1:
            range_ = parse_range(tokens[1])
        else:
            range_ = None
        if not range_:
            range_ = (group.low, group.high)
        if not range_[1]:
            range_ = (range_[0], group.high)
        ret = [f"211 {group.number} {group.low} {group.high} {group.name}"]
        for i in range(range_[0], typing.cast(int, range_[1]) + 1):
            try:
                _articleinfo = self.server.articles[i]
                ret.append(str(i))
            except NNTPArticleNotFound:
                pass
        ret.append(".")
        self.send_lines(ret)

    def list(self) -> None:
        self.server.refresh()
        command, *tokens = self.data.strip().split()
        keyword = tokens[0] if len(tokens) != 0 else None
        argument = tokens[1] if len(tokens) > 1 else None
        wildmat = tokens[2] if len(tokens) > 2 else None

        if keyword is None or keyword.casefold() == "active":
            if argument is None and wildmat is None:
                self.send_lines(
                    ["215 list of newsgroups follows"]
                    + [
                        f"{g.name} {g.high} {g.low} {g.posting_permitted}"
                        for g in self.server.groups.values()
                    ]
                    + ["."]
                )
                return
            if wildmat is None:
                self.send_lines(
                    ["215 list of newsgroups follows"]
                    + [
                        f"{g.name} {g.high} {g.low} {g.posting_permitted}"
                        for g in filter(
                            lambda g: g.name == argument, self.server.groups.values()
                        )
                    ]
                    + ["."]
                )
                return

        if keyword and keyword.casefold() == "newsgroups":
            if argument is None and wildmat is None:
                self.send_lines(
                    ["215 list of newsgroups follows"]
                    + [
                        f"{g.name}\t{g.short_description}"
                        for g in self.server.groups.values()
                    ]
                    + ["."]
                )
                return
            if wildmat is None:
                self.send_lines(
                    ["215 list of newsgroups follows"]
                    + [
                        f"{g.name} {g.high} {g.low} {g.posting_permitted}"
                        for g in filter(
                            lambda g: g.name == argument, self.server.groups.values()
                        )
                    ]
                    + ["."]
                )
                return

        self.send_lines(["501 Syntax Error"])
        return

    def select_group(self, group_name: str) -> bool:
        self.server.refresh()
        print("Group name", group_name)
        if group_name in self.server.groups:
            self.current_selected_newsgroup = group_name
            group = self.server.groups[group_name]
            if group.number == 0:
                self.current_article_number = None
            else:
                self.current_article_number = group.low
            self.send_lines(
                [f"211 {group.number} {group.low} {group.high} {group.name}"]
            )
            return True
        self.send_lines(["411 No such newsgroup"])
        return False

    def send_lines(self, lines: typing.List[str]) -> None:
        for line in lines:
            if self.server.debugging:
                print("sending", line)
            self.request.sendall(bytes(line.strip(), "utf-8") + _CRLF)

    def _getline(self, strip_crlf: bool = True) -> str:
        line = None
        if b"\n" in self._buffer:
            line = self._buffer[: self._buffer.find(b"\n")]
            self._buffer = self._buffer[len(line) + 1 :]
        while not line:
            chunk = self.request.recv(_MAXLINE + 1)
            self._buffer += chunk
            if b"\n" in chunk or not chunk.strip():
                break
        if b"\n" not in self._buffer and self._buffer:
            raise NNTPDataError("Too big a line.")
        if not line:
            line = self._buffer[: self._buffer.find(b"\n")]
            self._buffer = self._buffer[len(line) + 1 :]
        if line is None:
            raise EOFError
        if strip_crlf:
            if line[-2:] == _CRLF:
                line = line[:-2]
            elif line[-1:] in _CRLF:
                line = line[:-1]
        return line.decode("utf-8")

    def _getlines(self) -> str:
        lines = []
        while True:
            line = self._getline()
            if line == ".":
                break
            if line.startswith(".."):
                line = line[1:]
            lines.append(line)
        return "\n".join(lines)

    def hdr(self) -> None:
        def get_value(i: ArticleInfo, field: str) -> str:
            field = field.casefold()
            if field == "subject":
                value = articleinfo.subject
            elif field == "from":
                value = articleinfo.from_
            elif field == "date":
                value = email.utils.format_datetime(articleinfo.date)
            elif field == "message-id":
                value = articleinfo.message_id
            elif field == "references":
                value = articleinfo.references
            elif field == ":bytes":
                value = str(articleinfo.bytes)
            elif field == ":lines":
                value = str(articleinfo.lines)
            else:
                value = ""
                for k, v in articleinfo.headers.items():
                    if k.casefold() == field:
                        value = v
                        break
            value = value.replace("\r\n", "").replace("\t", " ")
            return value

        command, *tokens = self.data.strip().split()

        if len(tokens) == 0:
            self.send_lines(["501 Syntax Error"])
            return

        if len(tokens) == 2:
            range_ = parse_range(tokens[1])
            if not range_:
                # First form (message-id specified)
                try:
                    articleinfo = self.server.articles[tokens[1]]
                    value = get_value(articleinfo, tokens[0])
                    self.send_lines(
                        [
                            "225 Headers follow(multi-line)",
                            f"{articleinfo.number} {value}",
                            ".",
                        ]
                    )
                except NNTPArticleNotFound:
                    self.send_lines(["430 No article with that message-id"])
                return
            else:
                if not self.current_selected_newsgroup:
                    self.send_lines(["412 No newsgroup selected"])
                    return
                # Second form (range specified)
                if not range_[1]:
                    group = self.server.groups[
                        typing.cast(str, self.current_selected_newsgroup)
                    ]
                    range_ = (range_[0], group.high)
                ret = []
                for i in range(range_[0], typing.cast(int, range_[1]) + 1):
                    try:
                        articleinfo = self.server.articles[i]
                        value = get_value(articleinfo, tokens[0])
                        ret.append((articleinfo.number, value))
                    except NNTPArticleNotFound:
                        pass
                if len(ret) == 0:
                    self.send_lines(["423 No articles in that range"])
                    return
                self.send_lines(
                    ["225 Headers follow(multi-line)"]
                    + [f"{number} {value}" for (number, value) in ret]
                    + ["."]
                )
                return
            return

        if not self.current_selected_newsgroup:
            self.send_lines(["412 No newsgroup selected"])
            return
        # Third form (current article number used)
        try:
            articleinfo = self.server.articles[
                typing.cast(int, self.current_article_number)
            ]
        except:
            self.send_lines(["420 Current article number is invalid"])
            return

        value = get_value(articleinfo, tokens[0])

        self.send_lines(
            ["225 Headers follow(multi-line)", f"{articleinfo.number} {value}", "."]
        )

    def overview(self) -> None:
        if self.current_selected_newsgroup is None:
            self.send_lines(["412 No newsgroups elected"])
            return

        command, *tokens = self.data.split()
        if len(tokens) == 1:
            range_ = parse_range(tokens[0])
            if range_:
                if not range_[1]:
                    group = self.server.groups[self.current_selected_newsgroup]
                    range_ = (range_[0], group.high)
                ret = []
                for i in range(range_[0], typing.cast(int, range_[1]) + 1):
                    try:
                        ret.append(self.server.articles[i])
                    except NNTPArticleNotFound:
                        pass
                self.send_lines(
                    ["224 Overview information follows (multi-line)"]
                    + list(map(str, ret))
                    + ["."]
                )
                return
            try:
                article = self.server.articles[tokens[0]]
                self.send_lines(
                    ["224 Overview information follows (multi-line)"]
                    + list(map(str, [article]))
                    + ["."]
                )
            except NNTPArticleNotFound:
                self.send_lines(["430 No article with that message-id"])
            return
        if len(tokens) != 0:
            self.send_lines(["501 Syntax Error"])
            return

        if not self.current_article_number:
            self.send_lines(["420 Current article number is invalid"])
            return
        try:
            article = self.server.articles[self.current_article_number]
            self.send_lines(
                ["224 Overview information follows (multi-line)"]
                + list(map(str, [article]))
                + ["."]
            )
        except NNTPArticleNotFound:
            self.send_lines(["420 Current article number is invalid"])
        return

    def stat(self) -> None:
        self.server.refresh()
        command, *tokens = self.data.split()
        if len(tokens) == 0:
            if self.current_selected_newsgroup is None:
                self.send_lines(["412 No newsgroup elected"])
                return
            if self.current_article_number is None:
                self.send_lines(["420 Current article number is invalid"])
                return
            try:
                article = self.server.articles[self.current_article_number]
            except NNTPArticleNotFound:
                self.send_lines(["420 Current article number is invalid"])
                return
        else:
            try:
                number = int(tokens[0])
            except:
                self.send_lines(["501 Syntax Error"])
                return
            if number == 0:
                self.send_lines(["423 No article with that number"])
                return
            try:
                article = self.server.articles[number]
            except NNTPArticleNotFound:
                self.send_lines(["423 No article with that number"])
                return

        self.send_lines([f"223 {article.number} {article.message_id}"])
        return

    def article(self, body: bool = False) -> None:
        self.server.refresh()
        command, *tokens = self.data.split()
        if len(tokens) == 0:
            if self.current_selected_newsgroup is None:
                self.send_lines(["412 No newsgroup elected"])
                return
            if self.current_article_number is None:
                self.send_lines(["420 Current article number is invalid"])
                return
            try:
                article = self.server.article(self.current_article_number)
            except NNTPArticleNotFound:
                self.send_lines(["420 Current article number is invalid"])
                return
        else:
            try:
                try:
                    number = int(tokens[0])
                    if number == 0:
                        self.send_lines(["423 No article with that number"])
                        return
                    article = self.server.article(number)
                except ValueError:
                    article = self.server.article(tokens[0])
            except NNTPArticleNotFound:
                self.send_lines(["423 No article with that number"])
                return

        if body:
            ret = [f"222 {article.info.number} {article.info.message_id}"]
        else:
            ret = [
                f"220 {article.info.number} {article.info.message_id}",
                f"From: <{article.info.from_}>",
                f"Subject: {article.info.subject}",
                f"Date: {email.utils.format_datetime(article.info.date)}",
                f"Message-ID: {article.info.message_id}",
            ]
            if article.info.references:
                ret.append(f"References: {article.info.references}")
            ret += [f"{k}: {v}" for k, v in article.info.headers.items()]

            ret.append("")
        for line in article.body.split("\n"):
            if line.startswith("."):
                ret.append(f".{line}")
                continue
            ret.append(line)
        ret += ["."]
        self.send_lines(ret)

    def head(self) -> None:
        self.server.refresh()
        command, *tokens = self.data.split()
        if len(tokens) == 0:
            if self.current_selected_newsgroup is None:
                self.send_lines(["412 No newsgroup elected"])
                return
            if self.current_article_number is None:
                self.send_lines(["420 Current article number is invalid"])
                return
            try:
                article = self.server.article(self.current_article_number)
            except NNTPArticleNotFound:
                self.send_lines(["420 Current article number is invalid"])
                return
        else:
            try:
                try:
                    number = int(tokens[0])
                    if number == 0:
                        self.send_lines(["423 No article with that number"])
                        return
                    article = self.server.article(number)
                except ValueError:
                    article = self.server.article(tokens[0])
            except NNTPArticleNotFound:
                self.send_lines(["423 No article with that number"])
                return

        ret = [
            f"221 {article.info.number} {article.info.message_id}",
            f"From: <{article.info.from_}>",
            f"Subject: {article.info.subject}",
            f"Date: {email.utils.format_datetime(article.info.date)}",
            f"Message-ID: {article.info.message_id}",
        ]
        if article.info.references:
            ret.append(f"References: {article.info.references}")
        ret += [f"{k}: {v}" for k, v in article.info.headers.items()]
        ret += ["."]
        self.send_lines(ret)

    def help(self) -> None:
        import textwrap

        wrapper = textwrap.TextWrapper(width=50, replace_whitespace=False)

        server_help: typing.Optional[str] = self.server.help

        if server_help is None:
            server_help = """This is an NNTP version 2 server. You can list suggested newsgroups by issuing `LIST SUBSCRIPTIONS`.

You can list all groups by issuing `LIST NEWSGROUPS`.

You can select a group by issuing `GROUP ` followed by the group name. If successful, the groups total article count, lowest article number, highest article number and group name will be returned. You can use the high/low marks to fetch all articles or batches of them using the OVER command with `OVER ` followed by a number or a number plus as dash (e.g. `1-`) to indicate all numbers following or a number followed by dash followed by another number to indicate an inclusive range.

You can retrieve an article by issuing `ARTICLE ` followed by a message-id or a number."""

            if self.server.can_post:
                server_help += """

You can submit an article by issuing `POST` and the article content with each line terminated with <CR-LF>. End the article content with `<CR-LF>.<CR-LF>`."""
            if self.server.auth:
                server_help += """

You can authenticate by issuing `AUTHINFO USER ` followed by your username and then `AUTHINFO PASS ` followed by your password."""

        self.send_lines(["100 Help text follows"] + wrapper.wrap(server_help) + ["."])
