# Temporal_KGQA
Temporal KGQA

## Installation

Create a conda environment
``` 
conda create --prefix ./tkgqa_env 
conda activate ./tkgqa_env
```
Install tkbc requirements and setup tkbc
```
conda install --file requirements_tkbc.txt -c pytorch
python setup_tkbc.py install
```
Install Temporal KGQA requirements
```
conda install --file requirements.txt -c conda-forge
```

Download the dataset and models from <dataset_link> and unzip to create data and models directories.

To generate tkbc embeddings, you can run something like

```
CUDA_VISIBLE_DEVICES=5 python tkbc/learner.py --dataset wikidata_big --model \
TComplEx --rank 156 --emb_reg 1e-2 --time_reg 1e-2 \
--save_to tkbc_model_17dec.ckpt
```

Finally you can run training of QA model using these trained tkbc embeddings
```
CUDA_VISIBLE_DEVICES=1 python -W ignore ./train_qa_model.py --frozen 1 --eval_k 1 --max_epochs 200 \
 --lr 0.00002 --batch_size 250 --mode train --tkbc_model_file tkbc_model_17dec.ckpt \
 --dataset wikidata_big --valid_freq 3 --model eae_replace --valid_batch_size 50  \
 --save_to eae_replace_ckpt --lm_frozen 1
 ```

Note: If you get an error about not having GPU support, please install pytorch according to the CUDA version installed on the system. For eg. if you have CUDA 9.2
```
conda install pytorch torchvision torchaudio cudatoolkit=9.2 -c pytorch
```
