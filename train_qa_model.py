import argparse
from typing import Dict
import logging
import torch
from torch import optim
import pickle
import numpy as np

from qa_models import QA_model, QA_model_KnowBERT, QA_model_Only_Embeddings, QA_model_BERT, QA_model_EaE, QA_model_EmbedKGQA, QA_model_EaE_replace, QA_model_EmbedKGQA_single
from qa_datasets import QA_Dataset, QA_Dataset_model1, QA_Dataset_EaE, QA_Dataset_EmbedKGQA, QA_Dataset_EaE_replace, QA_Dataset_knowbert
from torch.utils.data import Dataset, DataLoader
import utils
from tqdm import tqdm
from utils import loadTkbcModel
from collections import defaultdict
from datetime import datetime
from collections import OrderedDict

parser = argparse.ArgumentParser(
    description="Temporal KGQA"
)
parser.add_argument(
    '--tkbc_model_file', default='model_tkbc_60kent.ckpt', type=str,
    help="Pretrained tkbc model checkpoint"
)

parser.add_argument(
    '--model', default='model1', type=str,
    help="Which model to use."
)

parser.add_argument(
    '--load_from', default='', type=str,
    help="Pretrained qa model checkpoint"
)

parser.add_argument(
    '--save_to', default='', type=str,
    help="Where to save checkpoint."
)

parser.add_argument(
    '--max_epochs', default=100, type=int,
    help="Number of epochs."
)

parser.add_argument(
    '--eval_k', default=1, type=int,
    help="Hits@k used for eval. Default 10."
)

parser.add_argument(
    '--valid_freq', default=5, type=int,
    help="Number of epochs between each valid."
)

parser.add_argument(
    '--num_transformer_heads', default=8, type=int,
    help="Num heads for transformer"
)

parser.add_argument(
    '--num_transformer_layers', default=6, type=int,
    help="Num layers for transformer"
)

parser.add_argument(
    '--batch_size', default=256, type=int,
    help="Batch size."
)

parser.add_argument(
    '--valid_batch_size', default=50, type=int,
    help="Valid batch size."
)

parser.add_argument(
    '--frozen', default=1, type=int,
    help="Whether entity/time embeddings are frozen or not. Default frozen."
)

parser.add_argument(
    '--lm_frozen', default=1, type=int,
    help="Whether language model params are frozen or not. Default frozen."
)

parser.add_argument(
    '--lr', default=2e-4, type=float,
    help="Learning rate"
)

parser.add_argument(
    '--mode', default='train', type=str,
    help="Whether train or eval."
)

parser.add_argument(
    '--eval_split', default='valid', type=str,
    help="Which split to validate on"
)

parser.add_argument(
    '--dataset_name', default='wikidata_big', type=str,
    help="Which dataset."
)

args = parser.parse_args()

# todo: this function may not be properly implemented
# might want to compare predicted khot with answers khot
# right now actual answers come from dataset.data[split][i]['answers']
# which works for now
# todo: eval batch size is fixed to 128 right now
def eval(qa_model, dataset, batch_size = 128, split='valid', k=10):
    num_workers = 4
    qa_model.eval()
    eval_log = []
    k_for_reporting = k # not change name in fn signature since named param used in places
    # k_list = [1, 3, 10]
    k_list = [1, 10]
    max_k = max(k_list)
    eval_log.append("Split %s" % (split))
    print('Evaluating split', split)

    data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, 
                            num_workers=num_workers, collate_fn=dataset._collate_fn)
    topk_answers = []
    total_loss = 0
    loader = tqdm(data_loader, total=len(data_loader), unit="batches")
    
    for i_batch, a in enumerate(loader):
        # if size of split is multiple of batch size, we need this
        # todo: is there a more elegant way?
        if i_batch * batch_size == len(dataset.data):
            break
        answers_khot = a[-1] # last one assumed to be target
        scores = qa_model.forward(a)
        for s in scores:
            pred = dataset.getAnswersFromScores(s, k=max_k)
            topk_answers.append(pred)
        loss = qa_model.loss(scores, answers_khot.cuda())
        total_loss += loss.item()
    eval_log.append('Loss %f' % total_loss)
    eval_log.append('Eval batch size %d' % batch_size)

    # do eval for each k in k_list
    # want multiple hit@k
    eval_accuracy_for_reporting = 0
    for k in k_list:
        hits_at_k = 0
        total = 0
        question_types_count = defaultdict(list)
        simple_complex_count = defaultdict(list)
        entity_time_count = defaultdict(list)

        for i, question in enumerate(dataset.data):
            actual_answers = question['answers']
            question_type = question['type']
            if 'simple' in question_type:
                simple_complex_type = 'simple'
            else:
                simple_complex_type = 'complex'
            entity_time_type = question['answer_type']
            # question_type = question['template']
            predicted = topk_answers[i][:k]
            if len(set(actual_answers).intersection(set(predicted))) > 0:
                val_to_append = 1
                hits_at_k += 1
            else:
                val_to_append = 0
            question_types_count[question_type].append(val_to_append)
            simple_complex_count[simple_complex_type].append(val_to_append)
            entity_time_count[entity_time_type].append(val_to_append)
            total += 1

        eval_accuracy = hits_at_k/total
        if k == k_for_reporting:
            eval_accuracy_for_reporting = eval_accuracy
        # eval_log.append('Hits at %d: %f' % (k, round(eval_accuracy, 3)))
        eval_log.append(str(round(eval_accuracy, 3)))


        question_types_count = dict(sorted(question_types_count.items(), key=lambda x: x[0].lower()))
        for dictionary in [question_types_count]:
        # for dictionary in [simple_complex_count, entity_time_count]:
            for key, value in dictionary.items():
                hits_at_k = sum(value)/len(value)
                s = '{q_type} \t {hits_at_k} \t total questions: {num_questions}'.format(
                    q_type = key,
                    hits_at_k = round(hits_at_k, 3),
                    num_questions = len(value)
                ) 
                # s = str(round(hits_at_k, 3))
                eval_log.append(s)
            eval_log.append('')        

    # print eval log as well as return it
    for s in eval_log:
        print(s)
    return eval_accuracy_for_reporting, eval_log

# def predict_single(qa_model, dataset, batch_size = 128, split='valid', k=10):

def predict_single(qa_model, dataset, ids, batch_size = 128, split='valid', k=10):
    num_workers = 4
    qa_model.eval()
    eval_log = []
    k_for_reporting = k # not change name in fn signature since named param used in places
    # k_list = [1, 3, 10]
    k_list = [1, 10]
    max_k = max(k_list)
    eval_log.append("Split %s" % (split))
    print('Evaluating split', split)

    # id = 13799        
        
    prepared_data = {}
    for k, v in dataset.prepared_data.items():
        prepared_data[k] = [v[i] for i in ids]
    dataset.prepared_data = prepared_data
    dataset.data = [dataset.data[i] for i in ids]

    dataset.print_prepared_data()

    data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, 
                            num_workers=num_workers, collate_fn=dataset._collate_fn)
    topk_answers = []
    total_loss = 0
    loader = tqdm(data_loader, total=len(data_loader), unit="batches")
    
    for i_batch, a in enumerate(loader):
        # if size of split is multiple of batch size, we need this
        # todo: is there a more elegant way?
        if i_batch * batch_size == len(dataset.data):
            break
        answers_khot = a[-1] # last one assumed to be target
        scores = qa_model.forward(a)
        for s in scores:
            pred = dataset.getAnswersFromScores(s, k=max_k)
            topk_answers.append(pred)
        loss = qa_model.loss(scores, answers_khot.cuda())
        total_loss += loss.item()
    eval_log.append('Loss %f' % total_loss)
    eval_log.append('Eval batch size %d' % batch_size)

    for i in range(len(dataset.data)):
        question = dataset.data[i]
        predicted_answers = topk_answers[i]
        actual_answers = question['answers']

        if question['answer_type'] == 'entity':
            actual_answers = [dataset.getEntityToText(x) for x in actual_answers]
            predicted_answers = [dataset.getEntityToText(x) for x in predicted_answers]

        print(question['paraphrases'][0])
        print('Actual answers', actual_answers)
        print('Predicted answers', predicted_answers)
        print()
    
    

    # do eval for each k in k_list
    # want multiple hit@k
    eval_accuracy_for_reporting = 0
    for k in k_list:
        hits_at_k = 0
        total = 0
        question_types_count = defaultdict(list)
        simple_complex_count = defaultdict(list)
        entity_time_count = defaultdict(list)

        for i, question in enumerate(dataset.data):
            actual_answers = question['answers']
            question_type = question['type']
            if 'simple' in question_type:
                simple_complex_type = 'simple'
            else:
                simple_complex_type = 'complex'
            entity_time_type = question['answer_type']
            # question_type = question['template']
            predicted = topk_answers[i][:k]
            if len(set(actual_answers).intersection(set(predicted))) > 0:
                val_to_append = 1
                hits_at_k += 1
            else:
                val_to_append = 0
            question_types_count[question_type].append(val_to_append)
            simple_complex_count[simple_complex_type].append(val_to_append)
            entity_time_count[entity_time_type].append(val_to_append)
            total += 1

        eval_accuracy = hits_at_k/total
        if k == k_for_reporting:
            eval_accuracy_for_reporting = eval_accuracy
        # eval_log.append('Hits at %d: %f' % (k, round(eval_accuracy, 3)))
        eval_log.append(str(round(eval_accuracy, 3)))


        question_types_count = dict(sorted(question_types_count.items(), key=lambda x: x[0].lower()))
        for dictionary in [question_types_count]:
        # for dictionary in [simple_complex_count, entity_time_count]:
            for key, value in dictionary.items():
                hits_at_k = sum(value)/len(value)
                s = '{q_type} \t {hits_at_k} \t total questions: {num_questions}'.format(
                    q_type = key,
                    hits_at_k = round(hits_at_k, 3),
                    num_questions = len(value)
                ) 
                # s = str(round(hits_at_k, 3))
                eval_log.append(s)
            eval_log.append('')        

    # print eval log as well as return it
    for s in eval_log:
        print(s)
    return eval_accuracy_for_reporting, eval_log


def append_log_to_file(eval_log, epoch, filename):
    f = open(filename, 'a+')
    now = datetime.now()
    dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
    f.write('Log time: %s\n' % dt_string)
    f.write('Epoch %d\n' % epoch)
    for line in eval_log:
        f.write('%s\n' % line)
    f.write('\n')
    f.close()

def train(qa_model, dataset, valid_dataset, args):
    num_workers = 5
    optimizer = torch.optim.Adam(qa_model.parameters(), lr=args.lr)
    optimizer.zero_grad()
    batch_size = args.batch_size
    data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers,
                            collate_fn=dataset._collate_fn)
    
    max_eval_score = 0
    if args.save_to == '':
        args.save_to = 'temp'
        
    result_filename = 'results/{dataset_name}/{model_file}.log'.format(
        dataset_name = args.dataset_name,
        model_file = args.save_to
    )
    checkpoint_file_name = 'models/{dataset_name}/qa_models/{model_file}.ckpt'.format(
        dataset_name = args.dataset_name,
        model_file = args.save_to
    )

    # if not loading from any previous file
    # we want to make new log file
    # also log the config ie. args to the file
    if args.load_from == '':
        print('Creating new log file')
        f = open(result_filename, 'w')
        now = datetime.now()
        dt_string = now.strftime("%d/%m/%Y %H:%M:%S")
        f.write('Log time: %s\n' % dt_string)
        f.write('Config: \n')
        for key, value in vars(args).items():
            key = str(key)
            value = str(value)
            f.write('%s:\t%s\n' % (key, value))
        f.write('\n')
        f.close()
    
    print('Starting training')
    for epoch in range(args.max_epochs):
        qa_model.train()
        epoch_loss = 0
        loader = tqdm(data_loader, total=len(data_loader), unit="batches")
        running_loss = 0
        for i_batch, a in enumerate(loader):
            qa_model.zero_grad()
            # question_tokenized = a[0]
            # question_attention_mask = a[1]
            # entities_times_padded = a[2]
            # entities_times_padded_mask = a[3]
            # answers_khot = a[4]
            # question_text = a[5]
            # TODO: depending on model, these variable names might not be representative
            # but trying to keep number of arguments constant across models
            # so that don't need 'if condition' here
            # TODO: pass variable 'a' and do splitting inside forward function
                        # scores = qa_model.forward(question_tokenized.cuda(), 
            #             question_attention_mask.cuda(), entities_times_padded.cuda(), 
            #             entities_times_padded_mask.cuda(), question_text)

            answers_khot = a[-1] # last one assumed to be target
            scores = qa_model.forward(a)

            loss = qa_model.loss(scores, answers_khot.cuda())
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
            running_loss += loss.item()
            loader.set_postfix(Loss=running_loss/((i_batch+1)*batch_size), Epoch=epoch)
            loader.set_description('{}/{}'.format(epoch, args.max_epochs))
            loader.update()

        print('Epoch loss = ', epoch_loss)
        if (epoch + 1) % args.valid_freq == 0:
            print('Starting eval')
            eval_score, eval_log = eval(qa_model, valid_dataset, batch_size=args.valid_batch_size, split=args.eval_split, k = args.eval_k)
            if eval_score > max_eval_score:
                print('Valid score increased') 
                save_model(qa_model, checkpoint_file_name)
                max_eval_score = eval_score
            # log each time, not max
            # can interpret max score from logs later
            append_log_to_file(eval_log, epoch, result_filename)


def save_model(qa_model, filename):
    print('Saving model to', filename)
    torch.save(qa_model.state_dict(), filename)
    print('Saved model to ', filename)
    return


tkbc_model = loadTkbcModel('models/{dataset_name}/kg_embeddings/{tkbc_model_file}'.format(
    dataset_name = args.dataset_name, tkbc_model_file=args.tkbc_model_file
))

if args.mode == 'test_kge':
    utils.checkIfTkbcEmbeddingsTrained(tkbc_model, args.dataset_name, args.eval_split)
    exit(0)

if args.model == 'model1':
    qa_model = QA_model(tkbc_model, args)
    dataset = QA_Dataset_model1(split='train', dataset_name=args.dataset_name)
    valid_dataset = QA_Dataset_model1(split=args.eval_split, dataset_name=args.dataset_name)
elif args.model == 'knowbert':
    qa_model = QA_model_KnowBERT(tkbc_model, args)
    dataset = QA_Dataset_knowbert(split='train', dataset_name=args.dataset_name)
    valid_dataset = QA_Dataset_knowbert(split=args.eval_split, dataset_name=args.dataset_name)
elif args.model == 'embedding_only':
    qa_model = QA_model_Only_Embeddings(tkbc_model, args)
    dataset = QA_Dataset_model1(split='train', dataset_name=args.dataset_name, tokenization_needed=False)
    valid_dataset = QA_Dataset_model1(split=args.eval_split, dataset_name=args.dataset_name)
elif args.model == 'bert':
    qa_model = QA_model_BERT(tkbc_model, args)
    dataset = QA_Dataset_model1(split='train', dataset_name=args.dataset_name)
    valid_dataset = QA_Dataset_model1(split=args.eval_split, dataset_name=args.dataset_name)
elif args.model == 'eae':
    qa_model = QA_model_EaE(tkbc_model, args)
    dataset = QA_Dataset_EaE(split='train', dataset_name=args.dataset_name)
    valid_dataset = QA_Dataset_EaE(split=args.eval_split, dataset_name=args.dataset_name)
elif args.model == 'eae_replace':
    qa_model = QA_model_EaE_replace(tkbc_model, args)
    dataset = QA_Dataset_EaE_replace(split='train', dataset_name=args.dataset_name)
    valid_dataset = QA_Dataset_EaE_replace(split=args.eval_split, dataset_name=args.dataset_name)
elif args.model == 'embedkgqa':
    qa_model = QA_model_EmbedKGQA(tkbc_model, args)
    dataset = QA_Dataset_EmbedKGQA(split='train', dataset_name=args.dataset_name)
    valid_dataset = QA_Dataset_EmbedKGQA(split=args.eval_split, dataset_name=args.dataset_name)
elif args.model == 'embedkgqa_single':
    qa_model = QA_model_EmbedKGQA_single(tkbc_model, args)
    dataset = QA_Dataset_EmbedKGQA(split='train', dataset_name=args.dataset_name)
    valid_dataset = QA_Dataset_EmbedKGQA(split=args.eval_split, dataset_name=args.dataset_name)

else:
    print('Model %s not implemented!' % args.model)
    exit(0)

print('Model is', args.model)


if args.load_from != '':
    filename = 'models/{dataset_name}/qa_models/{model_file}.ckpt'.format(
        dataset_name=args.dataset_name,
        model_file=args.load_from
    )
    print('Loading model from', filename)
    qa_model.load_state_dict(torch.load(filename))
    print('Loaded qa model from ', filename)
else:
    print('Not loading from checkpoint. Starting fresh!')

qa_model = qa_model.cuda()

if args.mode == 'eval':
    ids = [762, 13799, 22986, 26071]
    score, log = predict_single(qa_model, valid_dataset, ids=ids, batch_size=args.valid_batch_size, split=args.eval_split, k = args.eval_k)
    # score, log = eval(qa_model, valid_dataset, batch_size=args.valid_batch_size, split=args.eval_split, k = args.eval_k)
    exit(0)

train(qa_model, dataset, valid_dataset, args)

print('Training finished')
