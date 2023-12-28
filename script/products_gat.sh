python3.10 ../multigpu.py   --dataset_name "ogb-products" --model "gat" \
                            --gpu "0,1,2,3,4,5,6,7" --num_epochs 20 --hidden_dim 8 --head "4,4,1"

python3.10 ../multigpu.py   --dataset_name "ogb-products" --model "gat" \
                            --gpu "0,1,2,3" --num_epochs 20 --hidden_dim 8 --head "4,4,1"

python3.10 ../multigpu.py   --dataset_name "ogb-products" --model "gat" \
                            --gpu "0,1" --num_epochs 20 --hidden_dim 8 --head "4,4,1"

python3.10 ../multigpu.py   --dataset_name "ogb-products" --model "gat" \
                            --gpu "0" --num_epochs 20 --hidden_dim 8 --head "4,4,1"