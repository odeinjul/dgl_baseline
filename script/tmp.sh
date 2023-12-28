scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.16.164:/home/ubuntu/workspace/partition_dataset/ogb-paper100M-4p/part3 /home/ubuntu/workspace/partition_dataset/ogb-paper100M-4p/ 
scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.16.164:/home/ubuntu/workspace/partition_dataset/ogb-products-4p/ogb-products.json /home/ubuntu/workspace/partition_dataset/ogb-products-4p/ 
scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.16.164:/home/ubuntu/workspace/partition_dataset/ogb-paper100M-4p/ogb-paper100M.json /home/ubuntu/workspace/partition_dataset/ogb-paper100M-4p/ 
scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.16.164:/home/ubuntu/workspace/partition_dataset/mag240m-4p/mag240m.json /home/ubuntu/workspace/partition_dataset/mag240m-4p/ 
scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.16.164:/home/ubuntu/workspace/partition_dataset/mag240m-4p/part3 /home/ubuntu/workspace/partition_dataset/mag240m-4p/ 

scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.16.164:/home/ubuntu/workspace/partition_dataset/ogb-paper100M_undirected_graph.dgl /home/ubuntu/workspace/partition_dataset
scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.25.222:/home/ubuntu/workspace/partition_dataset/ogb-products_undirected_graph.dgl /home/ubuntu/workspace/partition_dataset
scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.25.222:/home/ubuntu/workspace/partition_dataset/mag240m_undirected_graph.dgl /home/ubuntu/workspace/partition_dataset
scp -i ~/.ssh/id_rsa_tmp -r ubuntu@172.31.16.164:/home/ubuntu/workspace/partition_dataset/friendster_graph.dgl /home/ubuntu/workspace/partition_dataset
