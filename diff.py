"""
    Compress the video through gradient-based optimization.
"""

import argparse
import gc
import logging
import time
from pathlib import Path
from typing import Tuple

import coloredlogs
import enlighten
import matplotlib.pyplot as plt
import seaborn as sns
import torch
import torch.nn.functional as F
import torchvision.transforms as T
from PIL import Image
from torch.utils.tensorboard import SummaryWriter
from utils.visualize_utils import visualize_heat_by_summarywriter
from torchvision import io
from datetime import datetime
import random

import yaml
from config import settings

from pdb import set_trace

from dnn.dnn_factory import DNN_Factory
from dnn.dnn import DNN
# from utils.results import write_results
from utils.video_reader import read_video, read_video_config
import utils.config_utils as conf
from collections import defaultdict
from tqdm import tqdm
from inference import inference, encode
from examine import examine
import pymongo
from munch import *

# from knob.control_knobs import framerate_control, quality_control

sns.set()


# set_trace()
conf.space = munchify(settings.configuration_space.to_dict())
state = {}

len_gt_video = 10
logger = logging.getLogger("diff")



# default_size = (800, 1333)
conf.serialize_order = list(conf.space.keys())



def augment(result, lengt):

    factor = (lengt + (len(result) - 1)) // len(result)

    return torch.cat([result[i // factor][None, :, :, :] for i in range(lengt)])




def read_expensive_from_config(gt_args: Munch, state, app: DNN, db: pymongo.database.Database) -> Tuple[dict, Munch]:

    average_video = None
    average_bw = 0
    sum_prob = 0

    # ret = defaultdict(lambda: 0)
    
    for args in conf.serialize_most_expensive_state(gt_args.copy(), conf.state2config(state), conf.serialize_order):

        # encode
        # args['gamma'] = 1.0
        video_name, remaining_frames = encode(args)
        video = list(read_video(video_name))
        video = torch.cat([i[1] for i in video])
        # video = F.interpolate(video, size=default_size)
        video = augment(video, len_gt_video)

        
        # video = video * prob

        # sum_prob += prob

        # if average_video is None:
        #     average_video = video
        # else:
        #     average_video = average_video + video

        # update statistics of random choice.
        stat = examine(args,gt_args,app,db)

        return stat, args, video


        # assert ret.keys() == {}.keys() or stat.keys() == ret.keys()

    #     for key in stat:
    #         if type(stat[key]) in [int, float]:
    #             ret[key] += stat[key] * prob

    #     Path(video_name).unlink()


    # ret.update({'video': average_video})
    # ret = dict(ret)
    # return ret


def optimize(args: dict, key: str, grad: torch.Tensor):

    # assert not hq_video.requires_grad

    

    configs = conf.space[key]
    args = args.copy()

    # set_trace()

    hq_index = configs.index(args[key])
    lq_index = hq_index + 1
    assert lq_index < len(configs)
    delta = 1.0 / (len(configs) - 1)
    x = state[key]
    if x.grad is None:
        x.grad = torch.zeros_like(x)

    def check():

        # logger.info(f'Index: HQ {hq_index} and LQ {lq_index}')

        logger.info(f'Searching {key} between HQ {configs[hq_index]} and LQ {configs[lq_index]}')
        
        args[key] = configs[lq_index]
        lq_name, lq_remaining_frames = encode(args)
        lq_video = torch.cat([i[1] for i in list(read_video(lq_name))])
        print(lq_remaining_frames)

        args[key] = configs[hq_index]
        hq_name, hq_remaining_frames = encode(args)
        hq_video = torch.cat([i[1] for i in list(read_video(hq_name))])
        print(hq_remaining_frames)

        

        if (hq_video - lq_video).abs().mean() > 1e-5: 

            logger.info('Search completed.')
            
            left, right = 1 - delta * hq_index, 1 - delta * lq_index
            assert left >= x > right

            

            x.grad += ( ((hq_video - lq_video) / (left - right)) * grad ).sum()

            return True

        else:

            return False   


    while (hq_index > 0 or lq_index < len(configs) - 1):

        if check():
            return
        
        hq_index -= 1
        hq_index = max(hq_index, 0)
        lq_index += 1
        lq_index = min(lq_index, len(configs) - 1)
    
    check()

                    

    
    



def main(args):

    # a bunch of initialization.
    
    torch.set_default_tensor_type(torch.FloatTensor)

    db = pymongo.MongoClient("mongodb://localhost:27017/")[settings.collection_name]

    app = DNN_Factory().get_model(settings.backprop.app)

    writer = SummaryWriter(f"runs/{args.output}")

    output_path = Path(args.output)
    if output_path.exists():
        output_path.unlink()
    logger.info("Application: %s", app.name)
    logger.info("Input: %s", args.input)
    logger.info("Output: %s", args.output)
    progress_bar = enlighten.get_manager().counter(
        total=args.sec,
        desc=f"{args.input}",
        unit="10frames",
    )

    if Path(args.output).exists():
        Path(args.output).unlink()
        
    # initialize configurations pace.
    for key in settings.backprop.tunable_config.keys():
        state[key] = torch.tensor(settings.backprop.tunable_config[key])


    # build optimizer
    for tensor in state.values():
        tensor.requires_grad = True
    optimizer = torch.optim.Adam(state.values(), lr=settings.backprop.lr)



    for sec in tqdm(range(args.sec)):

        progress_bar.update()

        logger.info('\nAt sec %d', sec)

        # for debugging purpose.
        # sec = 0
        
        gt_args = munchify(settings.ground_truths_config.to_dict()) 
        if 'fr' in gt_args.keys():
            del gt_args['fr']
        gt_args.update({
            'input': args.input,
            'second': sec
        })

        # construct average video and average bw
        ret, args, video = read_expensive_from_config(gt_args, state, app, db)
        
        # set_trace()
        # true_average_bw = ret['norm_bw'].item()
        # true_average_score = ret['mean_sum_score'].item()
        # true_average_f1 = ret['f1'].item()
        # true_average_std_score_mean = ret['std_sum_score'].item()
        # average_video = ret['video']
        # average_bw = ret['norm_bw']

        # print(average_bw)
        
        
        if 'gamma' in state:
            video = (video ** state['gamma']).clamp(0, 1)
            
            
        video.requires_grad = True
        scores = {}
        for idx, frame in enumerate(tqdm(video)):
            with torch.no_grad():
                result = app.inference(frame[None, :, :, :], detach=False, grad=False)
                score = torch.sum(result["instances"].scores)
                scores[idx] = score

        # interpolated_fr = conf.state2config(state)['fr']
        # interpolated_fr = interpolated_fr[0][0] * interpolated_fr[0][1] + interpolated_fr[1][0] * interpolated_fr[1][1]

        # set_trace()
        
        # interpolated_fr = ret['#remaining_frames']
        average_std_score_mean = torch.tensor([scores[i] for i in scores.keys()]).var(unbiased=False).detach()
        average_sum_score = torch.tensor([scores[i] for i in scores.keys()]).mean()
        sum_score = torch.tensor([scores[i] for i in scores.keys()]).detach().cpu()

        
        # print(interpolated_fr)
        # video.retain_grad()


        if settings.backprop.train:
            # backprop on bw
            # (args.bw_weight *  average_bw).backward(retain_graph=True)
            # backprop on std_score_mean, for the frame rate term.
            # (args.std_score_mean_weight * (torch.tensor([scores[i] for i in scores.keys()]).std(unbiased=False) / interpolated_fr)).backward(retain_graph=True)

            # backprop on each frame
            for idx, frame in enumerate(tqdm(video)):
                result = app.inference(frame[None, :, :, :], detach=False, grad=True)
                score = torch.sum(result["instances"].scores)

                partial_sum_score_mean = (1/len_gt_video) * score
                def temp(i):
                    if i != idx:
                        return scores[i]
                    else:
                        return score
                partial_std_score_mean = torch.cat([temp(i).unsqueeze(0) for i in scores.keys()]).var(unbiased=False)
                
                # set_trace()

                # set_trace()

                (-(settings.backprop.sum_score_mean_weight * partial_sum_score_mean + settings.backprop.std_score_mean_weight * partial_std_score_mean)).backward()
                # (-delta_sum_score).backward()

            # (settings.backprop.compute_weight * interpolated_fr).backward(retain_graph=True)
                
            # video.backward(video_detached.grad)


            # visualize_heat_by_summarywriter(T.ToPILImage()(average_video[0]), average_video.grad.abs().mean(dim=0).mean(dim=0), 'grad', writer, sec)

            # (average_video * average_video_detach).sum().backward(retain_graph = True)
            

            # if last_score is not None:
            #     if args.train:
            #         ( (-(1/len_gt_video) * last_score) + 25 * ((-1/(len_gt_video - 1)) * (score - last_score).abs()) ).backward(retain_graph=True)
            #     average_sum_score = average_sum_score +  ((1/len_gt_video) * last_score).item()
            #     average_delta_score = average_delta_score +  ((1/(len_gt_video - 1)) * (score - last_score).abs()).item()

            # last_score = score

        for key in settings.backprop.tunable_config.keys():
            if key == 'cloud_seg':
                optimize_cloudseg(args, video.grad)
            optimize(args, key, video.grad)

        # if args.train:
        #     (-(1/len_gt_video) * last_score).backward(retain_graph=True)
        # average_sum_score = average_sum_score +  (-(1/len_gt_video) * last_score).item()
            

        objective = (settings.backprop.sum_score_mean_weight * average_sum_score + settings.backprop.std_score_mean_weight * average_std_score_mean)
        # true_obj = (settings.backprop.sum_score_mean_weight * true_average_score + settings.backprop.std_score_mean_weight * true_average_std_score_mean  - settings.backprop.compute_weight * interpolated_fr.detach().item())
        
        
        state_str = ""
        for key in conf.serialize_order:
            logger.info('%s : %.3f, grad: %.7f', key, state[key], state[key].grad)
            state_str += '%s : %.3f, grad: %.7f\n' % (key, state[key], state[key].grad)

        # logger.info('QP: %.3f, Res: %.3f, Fr: %.3f', state['qp'], state['res'], state['fr'])
        # logger.info('qpgrad: %.3f, frgrad: %.3f, resgrad: %.3f', state['qp'].grad, state['fr'].grad, state['res'].grad)
        
        logger.info('Score: %.3f, std: %.3f, bw : %.3f, Obj: %.3f', average_sum_score, average_std_score_mean, ret['bw'], objective.item())

        # logger.info('True : %.3f, Tru: %.3f, Tru: %.3f, Tru: %.3f', true_average_score, true_average_std_score_mean, true_average_bw, true_obj)

        # optimize

        # truncate
        if settings.backprop.train and (sec + 1) % settings.backprop.freq == 0:
            optimizer.step()
            optimizer.zero_grad()
            
        for tensor in state.values():
            tensor.requires_grad = False
        for key in conf.serialize_order:
            if state[key] > 1. :
                state[key][()] = 1.
            if state[key] < 1e-7:
                state[key][()] = 1e-7
        for tensor in state.values():
            tensor.requires_grad = True
        

        
        logger.info(f'Current config: {conf.state2config(state)}')

        logger.info(f'Current state: {state}')

        # choose = conf.random_serialize(video_name, conf.state2config(state))

        
        # # logger.info('Choosing %s', choose)

        # with open(args.output, 'a') as f:
        #     f.write(yaml.dump([{
        #         'sec': sec,
        #         # 'choice': choose,
        #         'config': conf.state2config(state, serialize=True),
        #         'true_average_bw': true_average_bw,
        #         'true_average_score': true_average_score,
        #         'true_average_f1': true_average_f1,
        #         'fuse_obj': fuse_obj.item(),
        #         'true_obj': true_obj,
        #         'average_sum_score': average_sum_score.item(),
        #         'average_std_score_mean': average_std_score_mean.item(),
        #         'average_range_score_mean': ((sum_score.max() - sum_score.min()) / interpolated_fr).item(),
        #         'average_abs_score_mean': (sum_score - sum_score.mean()).abs().mean().item(),
        #         'state': state_str,
        #         # 'all_states': list(conf.serialize_all_states(args.input, conf.state2config(state, serialize=True), 1., conf.serialize_order)),
        #         # 'qp_grad': state['qp'].grad.item()
        #     }]))

        

        

        
            

        # set_trace()

    # for idx, (hqs, lqs) in enumerate(zip(read_video(args.hq, args), read_video(args.lq, args))):

    #     hqs = torch.cat([i[1] for i in hqs])
    #     lqs = torch.cat([i[1] for i in lqs])

    #     # frames = fr(q(hqs, lqs))
    #     frames = q(hqs, lqs)
    #     # frames = fr(hqs)


    #     for frame, hq in zip(frames, hqs):

    #         progress_bar.update()

    #         with torch.no_grad():
    #             result = app.inference(frame.unsqueeze(0), detach=True)
    #         # with torch.no_grad():
    #         #     hq_result = app.inference(hq.unsqueeze(0), detach=True)
    #         inference_results[fid] = result
            
    #         if idx % args.freq == 0:
    #             activation = app.activation(frame.unsqueeze(0))
    #             activation.backward(retain_graph=True)

    #         fid += 1

    #     if idx % args.freq == 0:
    #         # fr.step()
    #         q.step()

    #         image = F.interpolate(hqs, size=(480, 640))
    #         image = T.ToPILImage()(image[0])
    #         image = app.visualize(image, result, args)
    #         writer.add_image('inference', T.ToTensor()(image), fid)
            
    #         q.visualize(hqs[0], fid)

    #         means.append(q.q.detach().mean())


    mean = torch.tensor(means).mean().item()

    logger.info('Overall mean quality: %.3f', mean)

    # with open('config.yaml', 'a') as f:
    #     f.write(yaml.dump([{
    #         '#frames': fid,
    #         'bw': mean * Path(args.hq).stat().st_size + (1-mean) * Path(args.lq).stat().st_size,
    #         'video_name': args.output
    #     }]))

    # with open('diff.yaml', 'a') as f:
    #     f.write(yaml.dump({
    #         'acc': accs,
    #         'compute': computes,
    #         'size': sizes
    #     }))

    # print(torch.tensor(accs).mean() + torch.tensor(computes).mean() + torch.tensor(sizes).mean())
        


if __name__ == "__main__":

    # set the format of the logger
    coloredlogs.install(
        fmt="%(asctime)s [%(levelname)s] %(name)s:%(funcName)s[%(lineno)s] -- %(message)s",
        level="INFO",
    )

    parser = argparse.ArgumentParser()

    # parser.add_argument(
    #     "--freq",
    #     help="The video file names. The largest video file will be the ground truth.",
    #     default=1,
    #     type=int
    # )

    # parser.add_argument(
    #     '--lr',
    #     help='The learning rate',
    #     type=float,
    #     default=0.003
    # )

    # parser.add_argument(
    #     '--qp',
    #     help='The quantization parameter',
    #     type=float,
    #     default=1.
    # )

    # parser.add_argument(
    #     '--fr',
    #     help='The frame rate',
    #     type=float,
    #     default=1.
    # )

    # parser.add_argument(
    #     '--res',
    #     help='The resolution',
    #     type=float,
    #     default=1.
    # )

    parser.add_argument(
        '-i',
        '--input',
        help='The format of input video.',
        type=str,
        required=True
    )

    parser.add_argument(
        '--sec',
        help='The total secs of the video.',
        required=True,
        type=int
    )

    parser.add_argument(
        '-o',
        '--output',
        type=str,
        required=True
    )

    parser.add_argument(
        "--app", 
        type=str, 
        help="The name of the model.", 
        default='EfficientDet-d2',
    )
    # parser.add_argument(
    #     '--gamma',
    #     type=float,
    #     help='Adjust the luminance.',
    #     default=1.5,
    # )

    args = parser.parse_args()

    main(args)
