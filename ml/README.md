# ml/ — command → edit-ops model (M13)
Test set is written by humans and never generated. Training data may be synthetic.
Base model plan: Qwen3.5-2B → 4B, LoRA (full precision), Gemma 4 small as ablation.

# ml/critic/ — the design critic (M12)
Trained by a person, step by step: `make notebook` opens `walkthrough.ipynb`. Running it
builds the data, trains the model, tests it once on an unseen brand, and writes the model
(`stratum_worker/critic/model/critic-v1.json`, run by numpy on the server), the report
(`report-v1.json`) and the model card (`MODEL_CARD.md`, from `MODEL_CARD.template.md`).
