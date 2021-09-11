# `nntpserver.py`

No-dependency, single file NNTP server library for developing modern, rfc3977-compliant (bridge) NNTP servers for python >=3.7. Developed as part of [`tade`](https://github.com/epilys/tade), a web discussion forum with mailing list/NNTP interfaces which powers the https://sic.pm link aggregator.

Included example servers are:

- `example_server.py` returning hard-coded articles
- `hnnntp.py` querying news.ycombinator.com (hackernews) API and caching results in an sqlite3 database. A public instance *might* be online at nessuent.xyz:564 (TLS only)

<table align="center">
  <tbody>
    <tr>
      <td><p align="center" ><kbd ><img src="./commodore-amiga.png?raw=true" alt="screenshot of nntp server accessed via commodore amiga" title="screenshot of nntp server accessed via commodore amiga" height="300"  style="width: 100%; height: auto; " /></kbd></p></td>
    </tr>
    <tr>
      <th><sup>https://sic.pm NNTP server that uses `nntpserver.py` <br />accessed on a Commodore Amiga with <a href="http://newscoaster.sourceforge.net/">NewsCoaster</a> client</sup></th>
    </tr>
  </tbody>
</table>

Running `example_server.py`:

```shell
$ python3 example_server.py --connect-with-nntplib
Listening on localhost:9999
Connecting with nntplib...
New connection.
sending 201 NNTP Service Ready, posting prohibited
got: CAPABILITIES
sending 101 Capability list:
sending VERSION 2
sending READER
sending HDR
sending LIST ACTIVE NEWSGROUPS OVERVIEW.FMT SUBSCRIPTIONS
sending OVER
sending .
got: GROUP example.all
Group name example.all
sending 211 1 1 1 example.all
got: XOVER 1-1
sending 224 Overview information follows (multi-line)
sending 1       Hello world!    epilys <epilys@example.com>     Wed, 01 Sep 2021 15:06:01 +0000 <unique@example.com>            16      1
sending .
got: LIST OVERVIEW.FMT
sending 215 Order of fields in overview database.
sending Subject:
sending From:
sending Date:
sending Message-ID:
sending References:
sending Bytes:
sending Lines:
sending .
1 epilys <epilys@example.com> Hello world! (1)
Done with nntplib.
```
