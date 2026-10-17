"""Export HHEM-2.1-Open to ONNX without ``trust_remote_code`` (maintainer recipe; nothing in the app runs it).

Upstream (``vectara/hallucination_evaluation_model``, Apache-2.0 per its model card) is a custom
``HHEMv2ForSequenceClassification`` wrapper that holds a stock ``T5ForTokenClassification`` (the
FLAN-T5-base encoder plus a two-way linear head) and reads the logits at token position 0. ``optimum``
cannot export the wrapper, so this script rebuilds the stock module from the pinned
``google/flan-t5-base`` configuration, loads the upstream weights into it (keys ``t5.*``), and exports
the encoder plus head with ``[batch, 2]`` output: the logits at position 0 (``hallucinated``,
``consistent``). Inference needs only ``softmax(logits)[1]`` on the prompt below, so no custom code,
PyTorch or ``transformers`` is needed at run time (``byax.local_inference.EntailmentModel`` uses
``onnxruntime`` and ``tokenizers`` only).

Recipe (CPU only; run in a scratch environment outside the repository):

    uv venv hhem-export --python 3.12
    uv pip install --python hhem-export/bin/python --index-url https://download.pytorch.org/whl/cpu torch==2.14.1
    uv pip install --python hhem-export/bin/python "transformers==4.57.6" onnx==1.23.2 onnxruntime==1.31.0 \\
        onnxscript==0.7.2 sentencepiece protobuf numpy tokenizers safetensors
    hhem-export/bin/python scripts/export_hhem_onnx.py --out hhem-out

It downloads the two pinned revisions below (``--weights``/``--flan`` accept local directories), writes
``model-fp32.onnx``, ``model.onnx`` (dynamic int8 quantization of the matrix multiplications),
``tokenizer.json`` (the FLAN-T5 tokenizer) and ``config.json`` (prompt and parameters), prints their
SHA-256 and checks parity against the PyTorch reference on the model card's seven example pairs.
Upload the files you intend to ship to the ``models-v<N>`` mirror release under the catalogue's
``mirror_asset`` names and keep the model card and licence beside them. Never commit them.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

HF = 'https://huggingface.co'
HHEM_REVISION = 'de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8'
FLAN_REVISION = '7bcac572ce56db69c1ea7c8af255c5d7c9672fc2'
PROMPT = (
    '<pad> Determine if the hypothesis is true given the premise?\n\nPremise: {text1}\n\nHypothesis: {text2}'
)
# The model card's example pairs and the scores it publishes for them (``predict``, float32).
EXAMPLES = [
    ('The capital of France is Berlin.', 'The capital of France is Paris.', 0.0111),
    ('I am in California', 'I am in United States.', 0.6474),
    ('I am in United States', 'I am in California.', 0.1290),
    ('A person on a horse jumps over a broken down airplane.', 'A person is outdoors, on a horse.', 0.8969),
    (
        'A boy is jumping on skateboard in the middle of a red bridge.',
        'The boy skates down the sidewalk on a red bridge',
        0.1846,
    ),
    (
        'A man with blond-hair, and a brown shirt drinking out of a public water fountain.',
        'A blond man wearing a brown shirt is reading a book.',
        0.0050,
    ),
    ('Mark Wahlberg was a fan of Manny.', 'Manny was a fan of Mark Wahlberg.', 0.0543),
]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(repository, revision, name, directory):
    import urllib.request

    target = Path(directory) / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(f'{HF}/{repository}/resolve/{revision}/{name}', target)
    return target


def build_module(weights, flan_config):
    """The stock ``T5ForTokenClassification`` with HHEM's weights, wrapped to emit position-0 logits."""
    import torch
    from safetensors.torch import load_file
    from transformers import AutoConfig, T5ForTokenClassification

    config = AutoConfig.from_pretrained(flan_config)
    config.num_labels = 2
    model = T5ForTokenClassification(config)
    state = {k[len('t5.') :]: v for k, v in load_file(str(weights)).items() if k.startswith('t5.')}
    missing, unexpected = model.load_state_dict(state, strict=False)
    # T5 shares the (unused) token embedding with the encoder; anything else missing is an error.
    problems = [k for k in missing if k != 'transformer.encoder.embed_tokens.weight']
    if problems or unexpected:
        raise SystemExit(f'weights do not match the module: missing={problems} unexpected={unexpected}')
    model.eval()

    class Position0(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, input_ids, attention_mask):
            return self.inner(input_ids=input_ids, attention_mask=attention_mask).logits[:, 0, :]

    return Position0(model).eval()


def export(module, path):
    import torch

    ids = torch.tensor([[0, 363, 19, 8, 1], [0, 363, 1, 0, 0]], dtype=torch.long)
    mask = torch.tensor([[1, 1, 1, 1, 1], [1, 1, 1, 0, 0]], dtype=torch.long)
    torch.onnx.export(
        module,
        (ids, mask),
        str(path),
        input_names=['input_ids', 'attention_mask'],
        output_names=['logits'],
        dynamic_axes={
            'input_ids': {0: 'batch', 1: 'sequence'},
            'attention_mask': {0: 'batch', 1: 'sequence'},
            'logits': {0: 'batch'},
        },
        opset_version=17,
        dynamo=False,
    )


def quantize(source, target):
    """Dynamic int8 quantization of every MatMul except the feed-forward output projections (``wo``).

    Quantizing ``wo`` too is what breaks T5: its activations are large and the 8-bit range clips them.
    On the project's labelled set (``tests/fixtures/entailment``) the ranking quality for "supported
    against the rest" drops from an AUC of 0.948 (float32) to 0.900 and 26 of 244 verdicts flip at 0.5.
    Leaving those twelve layers in float32 keeps AUC at 0.948 with 5 flips, at 167 MB instead of 439.
    """
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic

    graph = onnx.load(str(source), load_external_data=False).graph
    skip = [n.name for n in graph.node if n.op_type == 'MatMul' and '/DenseReluDense/wo/' in n.name]
    quantize_dynamic(str(source), str(target), weight_type=QuantType.QInt8, nodes_to_exclude=skip)


def scores(path, tokenizer_path, pairs):
    import numpy as np
    import onnxruntime
    from tokenizers import Tokenizer

    session = onnxruntime.InferenceSession(str(path), providers=['CPUExecutionProvider'])
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    result = []
    for premise, hypothesis in pairs:
        encoding = tokenizer.encode(PROMPT.format(text1=premise, text2=hypothesis))
        ids = np.array([encoding.ids], dtype=np.int64)
        logits = session.run(None, {'input_ids': ids, 'attention_mask': np.ones_like(ids)})[0][0]
        exp = np.exp(logits - logits.max())
        result.append(float(exp[1] / exp.sum()))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--out', required=True, help='output directory (outside the repository)')
    parser.add_argument('--weights', help='directory holding the HHEM model.safetensors (default: download)')
    parser.add_argument('--flan', help='directory holding the google/flan-t5-base config and tokenizer')
    parser.add_argument('--skip-quantize', action='store_true')
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    weights = (
        Path(args.weights) / 'model.safetensors'
        if args.weights
        else fetch('vectara/hallucination_evaluation_model', HHEM_REVISION, 'model.safetensors', out / 'src')
    )
    flan = Path(args.flan) if args.flan else out / 'src'
    for name in ('config.json', 'tokenizer.json'):
        if not (flan / name).exists():
            fetch('google/flan-t5-base', FLAN_REVISION, name, flan)
    module = build_module(weights, flan / 'config.json')
    fp32 = out / 'model-fp32.onnx'
    export(module, fp32)
    (out / 'tokenizer.json').write_bytes((flan / 'tokenizer.json').read_bytes())
    (out / 'config.json').write_text(
        json.dumps(
            {
                'architecture': 'T5ForTokenClassification (FLAN-T5-base encoder + 2-way head), position 0',
                'prompt': PROMPT,
                'labels': ['hallucinated', 'consistent'],
                'weights': f'vectara/hallucination_evaluation_model@{HHEM_REVISION}',
                'tokenizer': f'google/flan-t5-base@{FLAN_REVISION}',
            },
            indent=2,
        )
        + '\n',
        encoding='utf-8',
    )
    files = [fp32]
    if not args.skip_quantize:
        quantize(fp32, out / 'model.onnx')
        files.append(out / 'model.onnx')
    pairs = [(a, b) for a, b, _ in EXAMPLES]
    published = [c for _, _, c in EXAMPLES]
    ok = True
    for path in files:
        got = scores(path, out / 'tokenizer.json', pairs)
        worst = max(abs(g - p) for g, p in zip(got, published, strict=True))
        print(
            f'{path.name}: {path.stat().st_size} bytes sha256 {sha256(path)} max |delta| vs card {worst:.4f}'
        )
        print('  scores', [round(g, 4) for g in got])
        if path == fp32:
            ok = ok and worst < 0.02
        else:  # quantized: the verdict at the 0.5 midpoint must not change on the card's examples
            ok = ok and all((g >= 0.5) == (p >= 0.5) for g, p in zip(got, published, strict=True))
    for name in ('tokenizer.json', 'config.json'):
        print(f'{name}: {(out / name).stat().st_size} bytes sha256 {sha256(out / name)}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
