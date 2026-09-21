# サンプルHTMLの置き場

`pokeca-box-hikaku.com` がネットワークポリシーで遮断されているため、
収集を自動化するにはページの中身を一度ここに置いてもらう必要がある。

## 置き方

ブラウザで BOX のページを開き（例: https://pokeca-box-hikaku.com/box/ninja-spinner.html）、
「ページを保存」または「ソースを表示 → 全選択 → 保存」で HTML を保存し、
ここに置いてコミットする。

```
data/samples/ninja-spinner.html
```

**1ページあれば十分。** 全BOXで同じ構造のはずなので、1つの実例から
パーサを書いて全ページに適用できる。

## そのあと

パーサを書いて `pokebox_ev/collect.py` として実装し、
ドメインが到達可能になった時点で日次収集に組み込む。
