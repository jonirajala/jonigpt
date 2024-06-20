import numpy as np
import torch
import tiktoken
import os
from torch import optim
from tqdm.auto import tqdm
from models import *
import json
import argparse
from datetime import datetime


def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def get_model(model_name):
    model_classes = {
        "gpt": GPT,
        "mistral": Mistral,
        "llama": LLama,
        "gemma": Gemma,
        "baseline_transformer": Transformer,
    }
    return model_classes.get(model_name, None)


class DataLoader:
    def __init__(self, data, batch_size, block_size):
        self.data = data

        self.batch_size = batch_size
        self.block_size = block_size
        self.pos = 0

    def get_batch(self):
        B, T = self.batch_size, self.block_size
        batch = self.data[self.pos : self.pos + B * T + 1]
        x = torch.tensor(batch[:-1], dtype=torch.long).reshape(B, T).to(device)
        y = torch.tensor(batch[1:], dtype=torch.long).reshape(B, T).to(device)
        self.pos += B * T

        if self.pos + (B * T + 1) > len(self.data):
            self.pos = 0

        return x, y


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load a specific model.")
    parser.add_argument("model_name", nargs="?", help="Name of the model to load")
    args = parser.parse_args()
    model_name = args.model_name

    os.makedirs("trained_models", exist_ok=True)
    os.makedirs("losses", exist_ok=True)

    enc = tiktoken.get_encoding("gpt2")
    device = "mps"

    class Config:
        vocab_size = enc.n_vocab
        block_size = 96
        window_size = block_size // 2
        batch_size = 32
        iters = 1000
        dropout = 0.1
        param_count = 75  # this sets how many million parameters each models has, currently either 50 or 75

    config = Config()

    train_data = np.array(
        np.memmap(
            os.path.join("data", "shakespare_train.bin"), dtype=np.uint16, mode="r"
        )
    )
    val_data = np.array(
        np.memmap(os.path.join("data", "shakespare_val.bin"), dtype=np.uint16, mode="r")
    )

    models = ["gpt", "llama", "mistral", "baseline_transformer", "gemma"]
    if model_name:
        assert model_name in models, f"Model {model_name} not found"
        models = [model_name]

    all_train_losses = {}
    all_val_losses = {}

    print(
        f"Training on {(config.batch_size * config.iters * config.block_size) // 1_000_000}M tokens"
    )

    for model_name in models:
        model = get_model(model_name)
        model = model(config).to(device)
        params = count_parameters(model)
        model_name = f"{model_name}-{params // 1_000_000}M"

        print(f"Model: {model_name:<10} | Params: {params:>10,}")

        optimizer = optim.AdamW(
            model.parameters(), lr=3e-4, weight_decay=0.1, betas=(0.9, 0.95)
        )

        trainloader = DataLoader(train_data, config.batch_size, config.block_size)
        valloader = DataLoader(val_data, config.batch_size, config.block_size)

        train_losses = []
        val_losses = []
        model.train()
        pbar = tqdm(range(config.iters), desc="Training Progress")
        for i in pbar:
            x, y = trainloader.get_batch()
            model.zero_grad()
            out, loss = model(x, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            pbar.set_postfix({"train_loss": loss.item()})
            train_losses.append(loss.item())

            if i % 50 == 0:
                model.eval()
                val_x, val_y = valloader.get_batch()
                with torch.no_grad():
                    val_out, val_loss = model(val_x, val_y)
                val_losses.append(val_loss.item())
                model.train()

        all_train_losses[model_name] = train_losses
        all_val_losses[model_name] = val_losses

        model_save_path = os.path.join(
            "trained_models", f"{model_name}_{config.iters}iters.pt"
        )
        torch.save(model.state_dict(), model_save_path)
        print(f"Model saved to {model_save_path}")

        model.eval()
        inp = torch.tensor(enc.encode("And that is  ")).to(device)
        gen_text = model.generate(inp).detach().cpu().numpy()
        gen_text = enc.decode(gen_text)
        print(gen_text)

    f_name = f"losses/{config.iters}_{datetime.now().strftime('%d-%m')}.json"
    with open(f_name, "w") as f:
        json.dump({"train_losses": all_train_losses, "val_losses": all_val_losses}, f)
