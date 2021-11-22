"""
    Compress the video through gradient-based optimization.
"""

import argparse
import gc
import logging
import time
from pathlib import Path

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

from pdb import set_trace

from dnn.dnn_factory import DNN_Factory
from utils.results import write_results
from utils.video_reader import read_video, read_video_config
import utils.config_utils as conf
from collections import defaultdict
from tqdm import tqdm

# from knob.control_knobs import framerate_control, quality_control

sns.set()



conf.space = {
    'qp': [24, 27, 30, 36],
    'fr': [30, 10, 3],
    'res': [720, 480, 360, 240]
    # 'qp': [24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36],
    # 'fr': [30],
    # 'res': [720],
}
state = {}

with open('stats_cityscape_0', 'r') as f:
    full_stats = yaml.safe_load(f.read())

'''
    def get_bw(x):
        return 123089.70337245 + x['qp'] * -6634.7401455 + x['res'] * 228.31799769 + x['fr'] * 3258.6768985

    plt.scatter( [i['bw'] for i in mpeg_conf], [-2.5/50000 * get_bw(i) + i['trunc_sum_score'] for i in mpeg_conf], c=[i['f1'] for i in mpeg_conf], cmap='Blues')
'''

default_size = (800, 1333)
conf.serialize_order = ['qp', 'fr', 'res']




def augment(result, lengt):

    factor = (lengt + (len(result) - 1)) // len(result)

    return torch.cat([result[i // factor][None, :, :, :] for i in range(lengt)])




def read_average_from_config(video_name, state, len_gt_video, gt_bw):

    def isfloat(x):
        try:
            float(x)
            return True
        except ValueError:
            return False
    average_video = None
    average_bw = 0
    sum_prob = 0

    ret = defaultdict(lambda: 0)
    
    for video_name, prob in conf.serialize_all_states(video_name, conf.state2config(state), 1., conf.serialize_order):

        # update statistics of linear combination
        
        video = list(read_video(video_name))
        video = torch.cat([i[1] for i in video])
        video = F.interpolate(video, size=default_size)
        video = augment(video, len_gt_video)

        
        bw = read_video_config(video_name)['bw'] / gt_bw

        video = video * prob

        # average_bw = max(average_bw, bw)
        
        bw = bw * 1.0 * prob
        sum_prob += prob

        # set_trace()

        if average_video is None:
            average_video = video
        else:
            average_video = average_video + video

        # update statistics of random choice.
        stat = conf.lookup(video_name, full_stats)


        # assert ret.keys() == {}.keys() or stat.keys() == ret.keys()

        for key in stat:
            if type(stat[key]) in [int, float]:
                ret['average_' + key] += stat[key] * prob


    ret.update({'average_video': average_video})
    ret = dict(ret)
    return ret



def main(args):

    # initialize
    logger = logging.getLogger("diff")
    torch.set_default_tensor_type(torch.FloatTensor)

    app = DNN_Factory().get_model(args.app)

    writer = SummaryWriter(f"runs/{args.output}")

    output_path = Path(args.output)
    if output_path.exists():
        output_path.unlink()
    # fr = framerate_control([1,3,5,15,])
    # q = quality_control(writer)

    # fid = 0
    # loss_weight = 400.

    logger.info("Application: %s", app.name)
    logger.info("Input: %s", args.input)
    logger.info("Output: %s", args.output)
    progress_bar = enlighten.get_manager().counter(
        total=args.sec,
        desc=f"{app.name}: {args.input}",
        unit="sec",
    )

    if Path(args.output).exists():
        Path(args.output).unlink()

    state['qp'] = torch.tensor(args.qp)
    state['res'] = torch.tensor(args.res)
    state['fr'] = torch.tensor(args.fr)


    # build optimizer for state
    for tensor in state.values():
        tensor.requires_grad = True

    optimizer = torch.optim.Adam(state.values(), lr=args.lr)

    logger.info(f'Training: {args.train}')



    for sec in tqdm(range(args.sec)):

        

        progress_bar.update()

        logger.info('\nAt sec %d', sec)

        # for debugging purpose.
        # sec = 0

        video_name = args.input % sec

        gt_config = read_video_config(conf.serialize_gt(video_name))
        len_gt_video = gt_config['#frames']
        gt_bw = gt_config['bw']

        # construct average video and average bw
        ret = read_average_from_config(video_name, state, len_gt_video, gt_bw)
        true_average_bw = ret['average_norm_bw'].item()
        true_average_score = ret['average_sum_score'].item()
        true_average_f1 = ret['average_f1'].item()
        true_average_std_score_mean = ret['average_std_score_mean'].item()
        average_video = ret['average_video']
        average_bw = ret['average_norm_bw']

        print(average_bw)
        # set_trace()
        
        # average_bw = 0
        # average_video = None
        # sum_prob = 0
        # true_average_bw = 0
        # true_average_score = 0
        # true_average_f1 = 0
        # true_average_std_score_mean = 0

        # for video_name, prob in serialize_all_states(video_name, state2config(state), 1., serialize_order):

        #     # update statistics of linear combination
            
        #     video = list(read_video(video_name))
        #     video = torch.cat([i[1] for i in video])
        #     video = F.interpolate(video, size=default_size)
        #     video = augment(video, len_gt_video)
            
        #     bw = read_video_config(video_name)['bw'] / gt_bw

        #     video = video * prob

        #     # average_bw = max(average_bw, bw)
            
        #     bw = bw * 1.0 * prob
        #     sum_prob += prob

        #     # set_trace()

        #     average_bw = average_bw + bw
        #     if average_video is None:
        #         average_video = video
        #     else:
        #         average_video = average_video + video

        #     # update statistics of random choice.
        #     true_bw, true_score, true_f1, true_std_score_mean = lookup(video_name)

        #     true_average_bw += true_bw * prob / gt_bw
        #     true_average_score += true_score * prob
        #     true_average_f1 += true_f1 * prob
        #     true_average_std_score_mean += true_std_score_mean * prob

        # inference on average video

        scores = {}
        for idx, frame in enumerate(tqdm(average_video)):
            with torch.no_grad():
                result = app.inference(frame[None, :, :, :], detach=False, grad=False)
                score = torch.sum(result["instances"].scores)
                scores[idx] = score

        # interpolated_fr = conf.state2config(state)['fr']
        # interpolated_fr = interpolated_fr[0][0] * interpolated_fr[0][1] + interpolated_fr[1][0] * interpolated_fr[1][1]
        
        interpolated_fr = ret['average_fr']
        average_std_score_mean = (torch.tensor([scores[i] for i in scores.keys()]).std(unbiased=False) / interpolated_fr.detach())
        
        average_sum_score = torch.tensor([scores[i] for i in scores.keys()]).mean()
        sum_score = torch.tensor([scores[i] for i in scores.keys()]).detach().cpu()

        
        # print(interpolated_fr)
        # average_video.retain_grad()


        if args.train:
            # backprop on bw
            (args.bw_weight *  average_bw).backward(retain_graph=True)
            # backprop on std_score_mean, for the frame rate term.
            (args.std_score_mean_weight * (torch.tensor([scores[i] for i in scores.keys()]).std(unbiased=False) / interpolated_fr)).backward(retain_graph=True)

            # backprop on each frame
            for idx, frame in enumerate(tqdm(average_video)):
                result = app.inference(frame[None, :, :, :], detach=False, grad=True)
                score = torch.sum(result["instances"].scores)

                delta_sum_score = (1/len_gt_video) * score
                def temp(i):
                    if i != idx:
                        return scores[i]
                    else:
                        return score
                std_score_mean = (torch.tensor([temp(i) for i in scores.keys()]).std(unbiased=False) / interpolated_fr.detach())

                (-(delta_sum_score - args.std_score_mean_weight * std_score_mean)).backward(retain_graph=True)


            # visualize_heat_by_summarywriter(T.ToPILImage()(average_video[0]), average_video.grad.abs().mean(dim=0).mean(dim=0), 'grad', writer, sec)

            # (average_video * average_video_detach).sum().backward(retain_graph = True)
            

            # if last_score is not None:
            #     if args.train:
            #         ( (-(1/len_gt_video) * last_score) + 25 * ((-1/(len_gt_video - 1)) * (score - last_score).abs()) ).backward(retain_graph=True)
            #     average_sum_score = average_sum_score +  ((1/len_gt_video) * last_score).item()
            #     average_delta_score = average_delta_score +  ((1/(len_gt_video - 1)) * (score - last_score).abs()).item()

            # last_score = score

        # if args.train:
        #     (-(1/len_gt_video) * last_score).backward(retain_graph=True)
        # average_sum_score = average_sum_score +  (-(1/len_gt_video) * last_score).item()
            

        fuse_obj = (average_sum_score - args.std_score_mean_weight * average_std_score_mean + (-args.bw_weight * average_bw))
        true_obj = (true_average_score - args.std_score_mean_weight * true_average_std_score_mean + (-args.bw_weight * true_average_bw))

        logger.info('QP: %.3f, Res: %.3f, Fr: %.3f', state['qp'], state['res'], state['fr'])
        logger.info('qpgrad: %.3f, frgrad: %.3f, resgrad: %.3f', state['qp'].grad, state['fr'].grad, state['res'].grad)
        
        logger.info('Score: %.3f, std: %.3f, bw : %.3f, Obj: %.3f', average_sum_score, average_std_score_mean, average_bw.item(), fuse_obj)

        logger.info('True : %.3f, Tru: %.3f, Tru: %.3f, Tru: %.3f', true_average_score, true_average_std_score_mean, true_average_bw, true_obj)

        # optimize

        # truncate
        if args.train and (sec + 1) % args.freq == 0:
            optimizer.step()
            optimizer.zero_grad()
            
        for tensor in state.values():
            tensor.requires_grad = False
        for key in conf.serialize_order:
            if state[key] > 1:
                state[key][()] = 1.
            if state[key] < 1e-7:
                state[key][()] = 1e-7
        for tensor in state.values():
            tensor.requires_grad = True
        

        
        logger.info(f'Current config: {conf.state2config(state)}')

        choose = conf.random_serialize(video_name, conf.state2config(state))

        
        # logger.info('Choosing %s', choose)

        with open(args.output, 'a') as f:
            f.write(yaml.dump([{
                'sec': sec,
                'choice': choose,
                'config': conf.state2config(state, serialize=True),
                'true_average_bw': true_average_bw,
                'true_average_score': true_average_score,
                'true_average_f1': true_average_f1,
                'fuse_obj': fuse_obj.item(),
                'true_obj': true_obj,
                'average_sum_score': average_sum_score.item(),
                'average_std_score_mean': average_std_score_mean.item(),
                'average_range_score_mean': ((sum_score.max() - sum_score.min()) / interpolated_fr).item(),
                'average_abs_score_mean': (sum_score - sum_score.mean()).abs().mean().item(),
                'all_states': list(conf.serialize_all_states(args.input, conf.state2config(state, serialize=True), 1., conf.serialize_order)),
                # 'qp_grad': state['qp'].grad.item()
            }]))

        

        

        
            

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

    parser.add_argument(
        "--freq",
        help="The video file names. The largest video file will be the ground truth.",
        default=1,
        type=int
    )

    parser.add_argument(
        '--lr',
        help='The learning rate',
        type=float,
        default=0.003
    )

    parser.add_argument(
        '--qp',
        help='The quantization parameter',
        type=float,
        default=1.
    )

    parser.add_argument(
        '--fr',
        help='The frame rate',
        type=float,
        default=1.
    )

    parser.add_argument(
        '--res',
        help='The resolution',
        type=float,
        default=1.
    )

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
        '--train',
        default=False,
        action='store_true',
    )

    parser.add_argument(
        '--batch_size',
        type=int,
        default=15
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
        default='COCO-Detection/faster_rcnn_R_101_FPN_3x.yaml',
    )

    parser.add_argument(
        '--std_score_mean_weight',
        type=float,
        help='The weight for delta_score term',
        default=15
    )
    parser.add_argument(
        '--bw_weight',
        type=float,
        help='The weight for bandwidth term',
        default=20
    )

    args = parser.parse_args()

    main(args)
