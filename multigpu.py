"""
This script trains and tests a GraphSAGE model for node classification on
multiple GPUs with distributed data-parallel training (DDP).

Before reading this example, please familiar yourself with graphsage node
classification using neighbor sampling by reading the example in the
`examples/sampling/node_classification.py`

This flowchart describes the main functional sequence of the provided example.
main
│
├───> Load and preprocess dataset
│
└───> run (multiprocessing) 
      │
      ├───> Init process group and build distributed SAGE model (HIGHLIGHT)
      │
      ├───> train
      │     │
      │     ├───> NeighborSampler
      │     │
      │     └───> Training loop
      │           │
      │           ├───> SAGE.forward
      │           │
      │           └───> Collect validation accuracy (HIGHLIGHT)
      │
      └───> layerwise_infer
            │
            └───> SAGE.inference
                  │
                  ├───> MultiLayerFullNeighborSampler
                  │
                  └───> Use a shared output tensor
"""
import argparse
import os
import time
import numpy as np
import random

import dgl
import dgl.nn as dglnn
from dgl.data.utils import load_graphs

import torch
import torch.distributed as dist
import torch.multiprocessing as mp
import torch.nn as nn
import torch.nn.functional as F
import torchmetrics.functional as MF
import tqdm
from dgl.data import AsNodePredDataset
from dgl.dataloading import (
    DataLoader,
    MultiLayerFullNeighborSampler,
    NeighborSampler,
)
from dgl.multiprocessing import shared_tensor
from ogb.nodeproppred import DglNodePropPredDataset
from torch.nn.parallel import DistributedDataParallel

log_path = ""

class SAGE(nn.Module):

    def __init__(self, in_size, hid_size, out_size):
        super().__init__()
        self.layers = nn.ModuleList()
        # Three-layer GraphSAGE-mean
        self.layers.append(dglnn.SAGEConv(in_size, hid_size, "mean"))
        self.layers.append(dglnn.SAGEConv(hid_size, hid_size, "mean"))
        self.layers.append(dglnn.SAGEConv(hid_size, out_size, "mean"))
        self.dropout = nn.Dropout(0.2)
        self.hid_size = hid_size
        self.out_size = out_size

    def forward(self, blocks, x):
        h = x
        for l, (layer, block) in enumerate(zip(self.layers, blocks)):
            h = layer(block, h)
            if l != len(self.layers) - 1:
                h = F.relu(h)
                h = self.dropout(h)
        return h

    def inference(self, g, device, batch_size, use_uva):
        g.ndata["h"] = g.ndata["features"]
        sampler = MultiLayerFullNeighborSampler(1, prefetch_node_feats=["h"])
        for l, layer in enumerate(self.layers):
            dataloader = DataLoader(
                g,
                torch.arange(g.num_nodes(), device=device),
                sampler,
                device=device,
                batch_size=batch_size,
                shuffle=False,
                drop_last=False,
                num_workers=0,
                use_ddp=True,  # use DDP
                use_uva=use_uva,
            )
            # In order to prevent running out of GPU memory, allocate a shared
            # output tensor 'y' in host memory.
            y = shared_tensor((
                g.num_nodes(),
                self.hid_size if l != len(self.layers) - 1 else self.out_size,
            ))
            for input_nodes, output_nodes, blocks in (tqdm.tqdm(dataloader)
                                                      if dist.get_rank() == 0
                                                      else dataloader):
                x = blocks[0].srcdata["h"]
                h = layer(blocks[0], x)  # len(blocks) = 1
                if l != len(self.layers) - 1:
                    h = F.relu(h)
                    h = self.dropout(h)
                # Non_blocking (with pinned memory) to accelerate data transfer
                y[output_nodes] = h.to(y.device, non_blocking=True)
            dist.barrier()
            g.ndata["h"] = y if use_uva else y.to(device)

        g.ndata.pop("h")
        return y

class GAT(nn.Module):
    def __init__(self,
                 in_size,
                 hid_size,
                 out_size,
                 n_heads,
                 activation=F.relu,
                 feat_dropout=0.6,
                 attn_dropout=0.6):
        n_layers = len(n_heads)
        assert n_heads[-1] == 1

        super().__init__()
        self.n_layers = n_layers
        self.hid_size = hid_size
        self.out_size = out_size
        self.n_heads = n_heads

        self.layers = nn.ModuleList()
        for i in range(0, n_layers):
            in_dim = in_size if i == 0 else hid_size * n_heads[i - 1]
            out_dim = out_size if i == n_layers - 1 else hid_size
            layer_activation = None if i == n_layers - 1 else activation
            self.layers.append(
                dglnn.GATConv(in_dim,
                              out_dim,
                              n_heads[i],
                              feat_drop=feat_dropout,
                              attn_drop=attn_dropout,
                              activation=layer_activation,
                              allow_zero_in_degree=True))

    
    def forward(self, blocks, inputs):
        h = inputs
        for i, (layer, block) in enumerate(zip(self.layers, blocks)):
            h = layer(block, h)
            if i == self.n_layers - 1:
                h = h.mean(1)
            else:
                h = h.flatten(1)
        return h
    
    def inference(self, g, device, batch_size, use_uva):
        g.ndata["h"] = g.ndata["features"]
        sampler = MultiLayerFullNeighborSampler(1, prefetch_node_feats=["h"])
        for l, layer in enumerate(self.layers):
            dataloader = DataLoader(
                g,
                torch.arange(g.num_nodes(), device=device),
                sampler,
                device=device,
                batch_size=batch_size,
                shuffle=False,
                drop_last=False,
                num_workers=0,
                use_ddp=True,  # use DDP
                use_uva=use_uva,
            )
            # In order to prevent running out of GPU memory, allocate a shared
            # output tensor 'y' in host memory.
            y = shared_tensor((
                g.num_nodes(),
                self.hid_size * self.n_heads[l] if l != len(self.layers) - 1 else self.out_size * self.n_heads[l],
            ))
            for input_nodes, output_nodes, blocks in (tqdm.tqdm(dataloader)
                                                      if dist.get_rank() == 0
                                                      else dataloader):
                x = blocks[0].srcdata["h"]
                h = layer(blocks[0], x)  # len(blocks) = 1
                if l != len(self.layers) - 1:
                    h = h.flatten(1)
                else:
                    h = h.mean(1)
                # Non_blocking (with pinned memory) to accelerate data transfer
                y[output_nodes] = h.to(y.device, non_blocking=True)
            dist.barrier()
            g.ndata["h"] = y if use_uva else y.to(device)

        g.ndata.pop("h")
        return y



def evaluate(device, model, g, num_classes, dataloader):
    model.eval()
    ys = []
    y_hats = []
    for it, (input_nodes, output_nodes, blocks) in enumerate(dataloader):
        with torch.no_grad():
            blocks = [block.to(device) for block in blocks]
            x = blocks[0].srcdata["features"]
            ys.append(blocks[-1].dstdata["labels"])
            y_hats.append(model(blocks, x))
    return MF.accuracy(
        torch.cat(y_hats),
        torch.cat(ys),
        task="multiclass",
        num_classes=num_classes,
    )


def layerwise_infer(proc_id,
                    device,
                    g,
                    num_classes,
                    nid,
                    model,
                    use_uva,
                    log_path,
                    batch_size=2**10):
    model.eval()
    with torch.no_grad():
        if not use_uva:
            g = g.to(device)
        pred = model.module.inference(g, device, batch_size, use_uva)
        pred = pred[nid]
        labels = g.ndata["labels"][nid].to(pred.device)
    if proc_id == 0:
        acc = MF.accuracy(pred,
                          labels,
                          task="multiclass",
                          num_classes=num_classes)
        with open(log_path, "a") as f:
            f.write(f"Test accuracy {acc.item():.4f}\n")
        print(f"Test accuracy {acc.item():.4f}")


def train(
    proc_id,
    nprocs,
    device,
    args,
    g,
    num_classes,
    train_idx,
    val_idx,
    model,
    use_uva,
):
    log_path = f"../logs/2023_12_28_t4_dgl_{args.dataset_name}_1x{nprocs}_{args.model}.log"
    # Instantiate a neighbor sampler
    sampler = NeighborSampler(
        [10, 10, 10],
        prefetch_node_feats=["features"],
        prefetch_labels=["labels"],
        fused=(args.mode != "benchmark"),
    )
    train_dataloader = DataLoader(
        g,
        train_idx,
        sampler,
        device=device,
        batch_size=1000,
        shuffle=True,
        drop_last=False,
        num_workers=args.num_workers,
        use_ddp=True,  # To split the set for each process
        use_uva=use_uva,
    )
    val_dataloader = DataLoader(
        g,
        val_idx,
        sampler,
        device=device,
        batch_size=1000,
        shuffle=True,
        drop_last=False,
        num_workers=args.num_workers,
        use_ddp=True,
        use_uva=use_uva,
    )
    opt = torch.optim.Adam(model.parameters(), lr=0.003, weight_decay=5e-4)
    time_count = 0
    for epoch in range(args.num_epochs):
        t0 = time.time()
        model.train()
        total_loss = 0
        for it, (input_nodes, output_nodes,
                 blocks) in enumerate(train_dataloader):
            x = blocks[0].srcdata["features"]
            y = blocks[-1].dstdata["labels"].to(torch.int64)
            y_hat = model(blocks, x)
            loss = F.cross_entropy(y_hat, y)
            opt.zero_grad()
            loss.backward()
            opt.step() 
            total_loss += loss
        acc = (evaluate(device, model, g, num_classes,
                        val_dataloader).to(device) / nprocs)
        t1 = time.time()
        time_count += (t1 - t0)
        dist.reduce(tensor=acc, dst=0)
        if proc_id == 0:
            print(f"Epoch {epoch:05d} | Loss {total_loss / (it + 1):.4f} | "
                  f"Accuracy {acc.item():.4f} | Time {t1 - t0:.4f}")
            with open(log_path, "a") as f:
                f.write(
                    f"Epoch {epoch:05d} | Loss {total_loss / (it + 1):.4f} | "
                    f"Accuracy {acc.item():.4f} | Time {t1 - t0:.4f}\n")
    tensor_time = torch.tensor(time_count).to(device)
    dist.reduce(tensor=tensor_time, dst=0)
    avg_time = tensor_time / nprocs / args.num_epochs
    if proc_id == 0:
        print(f"Avg epoch time: {avg_time}, Throughput: {len(train_idx) / avg_time:.4f}")
        with open(log_path, "a") as f:
            f.write(f"Avg epoch time: {avg_time}, Throughput: {len(train_idx) / avg_time:.4f}\n")


def run(proc_id, nprocs, devices, g, args):
    # Find corresponding device for current process.
    device = devices[proc_id]
    torch.cuda.set_device(device)
    #########################################################################
    # (HIGHLIGHT) Build a data-parallel distributed GraphSAGE model.
    #
    # DDP in PyTorch provides data parallelism across the devices specified
    # by the `process_group`. Gradients are synchronized across each model
    # replica.
    #
    # To prepare a training sub-process, there are four steps involved:
    # 1. Initialize the process group
    # 2. Unpack data for the sub-process.
    # 3. Instantiate a GraphSAGE model on the corresponding device.
    # 4. Parallelize the model with `DistributedDataParallel`.
    #
    # For the detailed usage of `DistributedDataParallel`, please refer to
    # PyTorch documentation.
    #########################################################################
    dist.init_process_group(
        backend="nccl",  # Use NCCL backend for distributed GPU training
        init_method="tcp://127.0.0.1:12345",
        world_size=nprocs,
        rank=proc_id,
    )
    num_classes = g.ndata["labels"].max().item() + 1
    train_idx = g.ndata.pop("train_mask").nonzero().squeeze()
    val_idx = g.ndata.pop("val_mask").nonzero().squeeze()
    test_idx = g.ndata.pop("test_mask").nonzero().squeeze()
    if args.mode != "benchmark":
        train_idx = train_idx.to(device)
        val_idx = val_idx.to(device)
        g = g.to(device if args.mode == "puregpu" else "cpu")
    in_size = g.ndata["features"].shape[1]
    if args.dataset_name == "ogb-paper100M":
        num_classes = 172
    # print(in_size, num_classes)
    if(args.model == "sage"):
        model = SAGE(in_size, args.hidden_dim, num_classes).to(device)
    elif(args.model == "gat"):
        model = GAT(in_size, args.hidden_dim, num_classes, args.head).to(device)
    model = DistributedDataParallel(model,
                                    device_ids=[device],
                                    output_device=device)

    # Training.
    use_uva = args.mode == "mixed"

    if proc_id == 0:
        print("Training...")
    train(
        proc_id,
        nprocs,
        device,
        args,
        g,
        num_classes,
        train_idx,
        val_idx,
        model,
        use_uva,
    )
    log_path = f"../logs/2023_12_28_t4_dgl_{args.dataset_name}_1x{nprocs}_{args.model}.log"

    # Testing.
    if proc_id == 0:
        print("Testing...")
    layerwise_infer(proc_id, device, g, num_classes, test_idx, model, use_uva, log_path=log_path)

    # Cleanup the process group.
    dist.destroy_process_group()



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        default="mixed",
        choices=["mixed", "puregpu", "benchmark"],
        help="Training mode. 'mixed' for CPU-GPU mixed training, "
        "'puregpu' for pure-GPU training.",
    )
    parser.add_argument(
        "--gpu",
        type=str,
        default="0",
        help="GPU(s) in use. Can be a list of gpu ids for multi-gpu training,"
        " e.g., 0,1,2,3.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="sage",
        help="GNN model",
    )
    parser.add_argument(
        "--num_epochs",
        type=int,
        default=10,
        help="Number of epochs for train.",
    )
    parser.add_argument(
        "--dataset_name",
        type=str,
        default="ogbn-products",
        help="Dataset name.",
    )
    parser.add_argument(
        "--dataset_dir",
        type=str,
        default="dataset",
        help="Root directory of dataset.",
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=0,
        help="Number of workers",
    )
    parser.add_argument(
        "--hidden_dim",
        type=int,
        default=32,
        help="Hidden dimension",
    )
    parser.add_argument(
        "--head",
        type=str,
        default="0",
        help="GAT head",
    )

    args = parser.parse_args()
    args.head =[int(head) for head in args.head.split(",")]
    devices = list(map(int, args.gpu.split(",")))
    nprocs = len(devices)
    assert (torch.cuda.is_available()
            ), f"Must have GPUs to enable multi-gpu training."
    print(f"Training in {args.mode} mode using {nprocs} GPU(s)")

    # Load and preprocess the dataset.
    print("Loading data")
    dataset = load_graphs(
        f"/home/ubuntu/workspace/partition_dataset/{args.dataset_name}_graph.dgl"
    )

    g = dataset[0][0]
    if args.dataset_name == "mag240m":
        g.ndata["features"] = np.random.rand(g.num_nodes(), 1).reshape(-1, 1).repeat(768, axis=1)
    # Explicitly create desired graph formats before multi-processing to avoid
    # redundant creation in each sub-process and to save memory.
    g.create_formats_()
    if args.dataset_name == "ogbn-arxiv":
        g = dgl.to_bidirected(g, copy_ndata=True)
        g = dgl.add_self_loop(g)
    # Thread limiting to avoid resource competition.
    os.environ["OMP_NUM_THREADS"] = str(mp.cpu_count() // 2 // nprocs)
    print("Preparing data")

    # To use DDP with n GPUs, spawn up n processes.
    mp.spawn(
        run,
        args=(nprocs, devices, g, args),
        nprocs=nprocs,
    )
