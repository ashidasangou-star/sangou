# 告知テキストの投入口

抽選・先着の告知本文をそのまま `.txt` で置く。ファイル名は何でもよい。

```bash
python3 -m lpwatch collect
```

読み終えたファイルは `done/` に移される（二度読まない）。
`--keep` を付けると移動しない。

X の投稿を貼る場合は、本文に LivePocket の URL
（`https://t.livepocket.jp/e/...`）が含まれている必要がある。
URL が無いテキストからは何も作らない。
