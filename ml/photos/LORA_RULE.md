# Stage 7 decision rule - written before any LoRA result

Ship the LoRA-tuned photo-type model only if all four hold:

1. Its macro-F1 gain over the linear probe (out-of-fold, the same 5 folds as stage 6) is at least 0.03.
2. The 95% paired-bootstrap interval of that gain is above 0.
3. It is not worse than the probe on the 24 client photos (probe: 21 of 24).
4. The gain is worth a second ~370 MB fp32 photo model and a second pass on every upload.

Otherwise ship the probe (types.json) and report LoRA as a measured experiment.

Settings fixed in advance: LoRA r=8, alpha=16, dropout 0.1 on q_proj and v_proj; 6 epochs;
batch 16; learning rates 2e-4 (adapters) and 1e-4 (head); head starts from the probe (LP-FT);
seed 0; C per fold = the C the probe chose.
