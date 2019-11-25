#!/bin/sh

export GPU_ID=$1

echo $GPU_ID

cd ..
export DATASET_DIR="../../datasets/"
export CUDA_VISIBLE_DEVICES=$GPU_ID
# Activate the relevant virtual environment:
python train_few_shot_system.py --name_of_args_json_file experiment_config/mini-imagenet_embedding_variant_intrinsic_5_way_5_vgg-based_shot_V2_preds_20_LSLR_conditioned_0.json --gpu_to_use $GPU_ID