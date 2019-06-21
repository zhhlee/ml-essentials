# ML Essentials

![](https://api.travis-ci.org/haowen-xu/ml-essentials.svg?branch=master)
![](https://coveralls.io/repos/github/haowen-xu/ml-essentials/badge.svg?branch=master)

A set of essential toolkits for daily machine learning experiments.

## Installation

```bash
pip install git+https://github.com/haowen-xu/ml-essentials.git
``` 

## Tutorials

### MLRunner

You may run an experiment with ML Runner, such that its information and output
can be saved via [ML Storage server](https://github.com/haowen-xu/mlstorage-server).

```bash
mlrun -s http://server:port -- python train.py
```

