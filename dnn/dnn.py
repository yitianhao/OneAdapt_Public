from abc import ABC, abstractmethod
from copy import deepcopy

import torch
from detectron2.data import MetadataCatalog
from detectron2.structures.boxes import pairwise_iou
from detectron2.utils.visualizer import Visualizer
from PIL import Image
from pdb import set_trace
from features.features import *
import numpy as np
from config import settings
from pycocotools.cocoeval import COCOeval

from munch import *

class DNN:
    # @abstractmethod
    # def cpu(self):
    #     pass

    # @abstractmethod
    # def cuda(self):
    #     pass


    def filter_result(
        self,
        result,
        gt=False,
        confidence_check=True,
        require_deepcopy=False,
        class_check=True,
        custom_confidence_threshold=None
    ):

        if require_deepcopy:
            result = deepcopy(result)

        scores = result["instances"].scores
        class_ids = result["instances"].pred_classes
        # if gt is False:
        #     print(scores)
        inds = scores < 0
        self.name = 'COCO-INSTANCESEGMENTATION/MASK_RCNN_R_50_FPN_1X'
        # print(self.name)
        if class_check:
            for i in getattr(settings, self.name).class_ids:
                inds = inds | (class_ids == i)
        else:
            inds = scores > -1


        args = getattr(settings, 'COCO-INSTANCESEGMENTATION/MASK_RCNN_R_50_FPN_1X')
        if confidence_check:
           if gt:
               inds = inds & (scores > args.gt_confidence_threshold)
           else:
               if custom_confidence_threshold is not None:
                   inds = inds & (scores > custom_confidence_threshold)
               else:
                   inds = inds & (scores > args.confidence_threshold)

        # inds = inds & (scores > getattr(settings, self.name).confidence_threshold)

        # result["instances"] = result["instances"][inds]

        return {"instances": result["instances"][inds]}

    def visualize(self, image, result):
        # set_trace()
        # result = self.filter_result(result, args, gt=gt)
        v = Visualizer(image, MetadataCatalog.get("coco_2017_train"), scale=1)
        out = v.draw_instance_predictions(result["instances"])
        return Image.fromarray(out.get_image(), "RGB")

    def calc_feature(self, result_dict):

        feature_list = []
        for key in result_dict:
            feature_list.append(get_frame_features(result_dict[key]["instances"].scores, getattr(settings, self.name)))
        feature_list = torch.cat(feature_list, dim=0)
        return get_features(feature_list, getattr(settings, self.name))

    def calc_accuracy(self, result_dict, gt_dict):
        if self.type == 'Segmentation':
            return self.calc_accuracy_segmentation(result_dict, gt_dict)
        if self.type == "Detection":
            return self.calc_accuracy_detection(result_dict, gt_dict)
        elif self.type == "Keypoint":
            return self.calc_accuracy_keypoint(result_dict, gt_dict)

    # def calc_accuracy_loss(self, image, gt, args):

    #     result = self.inference(image, detach=False, grad=True)

    #     if "Detection" in self.name:
    #         return self.calc_accuracy_loss_detection(result, gt, args)
    #     else:
    #         raise NotImplementedError()
    def calculate_iou_mask(self, mask1, mask2):
        iou_score = torch.zeros((mask1.shape[0], mask2.shape[0]))
        for i in range(mask1.shape[0]):
            for j in range(mask2.shape[0]):
                intersection = torch.logical_and(mask1[i], mask2[j])
                union = torch.logical_or(mask1[i], mask2[j])
                iou_score[i][j] = torch.sum(intersection).item() / torch.sum(union).item()

        return iou_score
    def compute_average_precision(self, precision, recall):
        """ Compute Avearage Precision by all points.
        Arguments:
            precision (np.array): precision values.
            recall (np.array): recall values.
        Returns:
            average_precision (np.array)
        """
        precision = np.concatenate(([0.], precision, [0.]))
        recall = np.concatenate(([0.], recall, [1.]))
        for i in range(precision.size - 1, 0, -1):
            precision[i - 1] = np.maximum(precision[i - 1], precision[i])
        ids = np.where(recall[1:] != recall[:-1])[0]
        average_precision = np.sum((recall[ids + 1] - recall[ids]) * precision[ids + 1])
        return average_precision

    def calculate_map(self, pairwise_iou, gt, result):
        iou_thresholds = np.arange(0.5, 0.95, 0.05)
        # print(pairwise_iou)
        map = []
        for cls_index in getattr(settings, self.name).class_ids:
            prec = []
            rec = []
            for iou_threshold in iou_thresholds:
                tp = 0
                fp = 0
                fn = 0

                for i in range(len(result.pred_classes)):
                    if result.pred_classes[i] != cls_index: continue
                    if len(pairwise_iou[i, :]) == 0:
                        continue
                    max_iou = max(pairwise_iou[i, :])
                    max_j = pairwise_iou[i, :].argmax()

                    if max_iou >= iou_threshold:
                        if gt.pred_classes[max_j] == result.pred_classes[i]:
                            tp += 1
                        else:
                            fp += 1
                for j in range(len(gt.pred_classes)):
                    if gt.pred_classes[j] != cls_index: continue
                    max_iou = max(pairwise_iou[:, j])
                    max_i = pairwise_iou[:, j].argmax()
                    if len(pairwise_iou[:, j]) == 0:
                        continue
                    if max_iou >= iou_threshold:
                        if gt.pred_classes[j] != result.pred_classes[max_i]:
                            fn += 1
                    else:
                        fn += 1
                if tp == 0:
                    if np.sum(gt.pred_classes.cpu().numpy() == cls_index) == 0:
                        prec.append(1)
                        rec.append(1)
                    else:
                        prec.append(0)
                        rec.append(0)

                else:
                    prec.append(tp/(tp+fp))
                    rec.append(tp/(tp+fn))



            map.append(self.compute_average_precision(prec, rec))
        return np.array(map).mean()

    def calculate_miou(self, gt, result):
        mean_iou = []
        if len(gt.pred_masks) == 0:
            if len(result.pred_masks) > 0:
                return 0
            else:
                return 1
        if len(result.pred_masks) == 0:
            if len(gt.pred_masks) > 0:
                return 0
            else:
                return 1
        for cls_name in getattr(settings, self.name).class_ids:
            prediction_mask = torch.zeros(gt.pred_masks[0].shape)
            gt_mask = torch.zeros(gt.pred_masks[0].shape)

            for idx in range(len(result.pred_masks)):
                if result.pred_classes[idx] == cls_name:
                    prediction_mask = torch.logical_or(prediction_mask, result.pred_masks[idx] )
            for  idx in range(len(gt.pred_masks)):
                if gt.pred_classes[idx] == cls_name:
                    gt_mask = torch.logical_or(gt_mask, gt.pred_masks[idx] )
            intersection = torch.logical_and(prediction_mask,gt_mask)
            union = torch.logical_or(prediction_mask,gt_mask)
            if torch.sum(union).item() == 0:
                continue
            iou = torch.sum(intersection).item() / torch.sum(union).item()
            mean_iou.append(iou)
        return sum(mean_iou) / len(mean_iou)
    def calc_accuracy_segmentation(self, result_dict, gt_dict):

        assert (
            result_dict.keys() == gt_dict.keys()
        ), "Result and ground truth must contain the same number of frames."
        print("In calculate accuracy!!!!!!!!")
        f1s = []
        prs = []
        res = []
        tps = []
        fps = []
        fns = []
        ious = []
        ret_dict = {}
        maps = []
        for fid in result_dict.keys():
            result = result_dict[fid]
            gt = gt_dict[fid]

            result = self.filter_result(result, False)
            gt = self.filter_result(gt, True)

            result = result["instances"]
            gt = gt["instances"]
            if len(result) == 0:
                if len(gt) == 0:
                    ious.append(1)
                else:
                    ious.append(0)
                continue
            ious.append(self.calculate_miou(gt, result))

            iou_pairwise = self.calculate_iou_mask(result.pred_masks, gt.pred_masks)
            # # ious.append(self.calculate_miou(iou_pairwise, gt, result))
            maps.append(self.calculate_map(iou_pairwise, gt, result))


        ret_dict['f1'] = sum(ious)/len(ious)
        if len(maps) == 0:
            ret_dict['map'] = 0 
        else:
            ret_dict['map'] = sum(maps)/len(maps)

        return ret_dict

    def calc_accuracy_detection(self, result_dict, gt_dict):

        assert (
            result_dict.keys() == gt_dict.keys()
        ), "Result and ground truth must contain the same number of frames."

        f1s = []
        prs = []
        res = []
        tps = []
        fps = []
        fns = []

        for fid in result_dict.keys():
            result = result_dict[fid]
            gt = gt_dict[fid]

            result = self.filter_result(result, False)
            gt = self.filter_result(gt, True)

            result = result["instances"]
            gt = gt["instances"]

            if len(result) == 0 or len(gt) == 0:
                if len(result) == 0 and len(gt) == 0:
                    f1s.append(1.0)
                    prs.append(1.0)
                    res.append(1.0)
                else:
                    f1s.append(0.0)
                    prs.append(0.0)
                    res.append(0.0)

            IoU = pairwise_iou(result.pred_boxes, gt.pred_boxes)

            for i in range(len(result)):
                for j in range(len(gt)):
                    if result.pred_classes[i] != gt.pred_classes[j]:
                        IoU[i, j] = 0

            tp = 0

            for i in range(len(gt)):
                if sum(IoU[:, i] > getattr(settings, self.name).iou_threshold):
                    tp += 1
            fn = len(gt) - tp
            fp = len(result) - tp
            fp = max(fp, 0)

            if 2 * tp + fp + fn == 0:
                f1 = 1.0
            else:
                f1 = 2 * tp / (2 * tp + fp + fn)
            if tp + fp == 0:
                pr = 1.0
            else:
                pr = tp / (tp + fp)
            if tp + fn == 0:
                re = 1.0
            else:
                re = tp / (tp + fn)

            f1s.append(f1)
            prs.append(pr)
            res.append(re)
            tps.append(tp)
            fps.append(fp)
            fns.append(fn)

        sum_tp = sum(tps)
        sum_fp = sum(fps)
        sum_fn = sum(fns)

        if 2 * sum_tp + sum_fp + sum_fn == 0:
            sum_f1 = 1.0
        else:
            sum_f1 = 2 * sum_tp / (2 * sum_tp + sum_fp + sum_fn)

        ret_dict = {
            "f1": torch.tensor(f1s).mean().item(),
            "pr": torch.tensor(prs).mean().item(),
            "re": torch.tensor(res).mean().item(),
            "tp": torch.tensor(tps).sum().item(),
            "fp": torch.tensor(fps).sum().item(),
            "fn": torch.tensor(fns).sum().item(),
            "sum_f1": sum_f1
            # "f1s": f1s,
            # "prs": prs,
            # "res": res,
            # "tps": tps,
            # "fns": fns,
            # "fps": fps,
        }

        ret_dict.update(self.calc_feature(result_dict))

        return ret_dict



    def calc_accuracy_detection_direct(self, result_dict, gt_dict):

        assert (
            result_dict.keys() == gt_dict.keys()
        ), "Result and ground truth must contain the same number of frames."

        f1s = []
        prs = []
        res = []
        tps = []
        fps = []
        fns = []
        fn_origs = []
        fn_hiddens = []
        for fid in result_dict.keys():
            result = result_dict[fid]
            gt = gt_dict[fid]

            result = self.filter_result(result, False)
            result_hidden = self.filter_result(result_dict[fid], False, custom_confidence_threshold=0)
            gt = self.filter_result(gt, True)

            result = result["instances"]
            gt = gt["instances"]
            result_hidden = result_hidden["instances"]

            if len(result) == 0 or len(gt) == 0:
                if len(result) == 0 and len(gt) == 0:
                    f1s.append(1.0)
                    prs.append(1.0)
                    res.append(1.0)
                else:
                    f1s.append(0.0)
                    prs.append(0.0)
                    res.append(0.0)

            IoU = pairwise_iou(result.pred_boxes, gt.pred_boxes)

            for i in range(len(result)):
                for j in range(len(gt)):
                    if result.pred_classes[i] != gt.pred_classes[j]:
                        IoU[i, j] = 0

            IoU_hidden = pairwise_iou(result_hidden.pred_boxes, gt.pred_boxes)

            for i in range(len(result_hidden)):
                for j in range(len(gt)):
                    if result_hidden.pred_classes[i] != gt.pred_classes[j]:
                        IoU_hidden[i, j] = 0
            tp = 0
            tp_hidden = 0
            # print("Length of iou: ", IoU.shape)
            # print("Length of iou hidden: ", IoU_hidden.shape)
            fn_hidden = 0
            for i in range(len(gt)):
                if sum(IoU[:, i] > getattr(settings, self.name).iou_threshold):
                    tp += 1
                if sum(IoU_hidden[:, i] > getattr(settings, self.name).iou_threshold) > 0:
                    tp_hidden += 1
                elif sum(IoU_hidden[:, i] > getattr(settings, self.name).iou_threshold) == 0:
                    fn_hidden += 1
            assert tp <= tp_hidden
            if (len(gt) - tp) > 0:
                fn_hidden_ratio = fn_hidden / (len(gt) - tp)
            else:
                fn_hidden_ratio = 0

            fn_hiddens.append(fn_hidden_ratio)
            fn = len(gt) - tp_hidden
            fp = len(result) - tp
            fp = max(fp, 0)

            if 2 * tp + fp + fn == 0:
                f1 = 1.0
            else:
                f1 = 2 * tp / (2 * tp + fp + fn)
            if tp + fp == 0:
                pr = 1.0
            else:
                pr = tp / (tp + fp)
            if tp + fn == 0:
                re = 1.0
            else:
                re = tp_hidden / (tp_hidden + fn)
                re_orig = tp / len(gt)
            f1s.append(f1)
            prs.append(pr)
            res.append(re)
            tps.append(tp)
            fps.append(fp)
            fns.append(fn)
            fn_origs.append(re_orig)

        sum_tp = sum(tps)
        sum_fp = sum(fps)
        sum_fn = sum(fns)

        if 2 * sum_tp + sum_fp + sum_fn == 0:
            sum_f1 = 1.0
        else:
            sum_f1 = 2 * sum_tp / (2 * sum_tp + sum_fp + sum_fn)
        print("relaxed recall: ", res)
        print(" recall: ", fn_origs)

        print("hidden fns", fn_hiddens)
        print("++++++++")
        ret_dict = {
            "f1_debug": torch.tensor(f1s).mean().item(),
            "pr": torch.tensor(prs).mean().item(),
            "re": torch.tensor(res).mean().item(),
            "fn_hidden_ratio": np.mean(np.array(fn_hiddens))
            # "f1s": f1s,
            # "prs": prs,
            # "res": res,
            # "tps": tps,
            # "fns": fns,
            # "fps": fps,
        }

        ret_dict.update(self.calc_feature(result_dict))

        return ret_dict
    def calc_accuracy_keypoint(self, result_dict, gt_dict, args):
        f1s = []
        # prs = []
        # res = []
        # tps = []
        # fps = []
        # fns = []
        for fid in result_dict.keys():
            result = result_dict[fid]["instances"].get_fields()
            gt = gt_dict[fid]["instances"].get_fields()
            if len(gt["scores"]) == 0 and len(result["scores"]) == 0:
                # prs.append(0.0)
                # res.append(0.0)
                f1s.append(1.0)
                # tps.append(0.0)
                # fps.append(0.0)
                # fns.append(0.0)
            elif len(result["scores"]) == 0 or len(gt["scores"]) == 0:
                # prs.append(0.0)
                # res.append(0.0)
                f1s.append(0.0)
                # tps.append(0.0)
                # fps.append(0.0)
                # fns.append(0.0)
            else:
                video_ind_res = result["scores"] == torch.max(result["scores"])
                kpts_res = result["pred_keypoints"][video_ind_res]
                video_ind_gt = gt["scores"] == torch.max(gt["scores"])
                kpts_gt = gt["pred_keypoints"][video_ind_gt]

                try:
                    acc = kpts_res - kpts_gt
                except:
                    import pdb

                    pdb.set_trace()
                    print("shouldnt happen")

                gt_boxes = gt["pred_boxes"][video_ind_gt].tensor
                kpt_thresh = float(args.dist_thresh)

                acc = acc[0]
                acc = torch.sqrt(acc[:, 0] ** 2 + acc[:, 1] ** 2)
                # acc[acc < kpt_thresh * kpt_thresh] = 0
                for i in range(len(acc)):
                    max_dim = max(
                        (gt_boxes[i // 17][2] - gt_boxes[i // 17][0]),
                        (gt_boxes[i // 17][3] - gt_boxes[i // 17][1]),
                    )
                    if acc[i] < (max_dim * kpt_thresh) ** 2:
                        acc[i] = 0

                accuracy = 1 - (len(acc.nonzero()) / acc.numel())
                # prs.append(0.0)
                # res.append(0.0)
                f1s.append(accuracy)
                # tps.append(0.0)
                # fps.append(0.0)
                # fns.append(0.0)

        return {
            "f1": torch.tensor(f1s).mean().item(),
            # "pr": torch.tensor(prs).mean().item(),
            # "re": torch.tensor(res).mean().item(),
            # "tp": torch.tensor(tps).sum().item(),
            # "fp": torch.tensor(fps).sum().item(),
            # "fn": torch.tensor(fns).sum().item(),
            # "f1s": f1s,
            # "prs": prs,
            # "res": res,
            # "tps": tps,
            # "fns": fns,
            # "fps": fps,
        }

    def get_undetected_ground_truth_index(self, result, gt):

        # if self.type == "Segmentation":
        #     raise NotImplementedError
        args = getattr(settings, self.name)
        gt = deepcopy(gt)
        result = deepcopy(result)
        result_orig = deepcopy(result)
        gt = self.filter_result(gt, gt=True)
        result = self.filter_result(result, gt=False)
        result_hidden = self.filter_result(result_orig, gt=False, custom_confidence_threshold=0)

        result = result["instances"]
        gt = gt["instances"]
        result_hidden = result_hidden['instances']
        fn_scores_ind_pred = []

        IoU = pairwise_iou(result.pred_boxes, gt.pred_boxes)
        for i in range(len(result)):
            for j in range(len(gt)):
                if result.pred_classes[i] != gt.pred_classes[j]:
                    IoU[i, j] = 0

        IoU_hidden = pairwise_iou(result_hidden.pred_boxes, gt.pred_boxes)
        for i in range(len(result_hidden)):
            for j in range(len(gt)):
                if result_hidden.pred_classes[i] != gt.pred_classes[j]:
                    IoU_hidden[i, j] = 0

        for i in range(len(result_hidden)):
            for j in range(len(gt)):
                if IoU_hidden[i][j] > getattr(settings, self.name).iou_threshold and result_hidden.scores[i] < args.confidence_threshold:
                    fn_scores_ind_pred.append(i)
        return (
            (IoU > getattr(settings, self.name).iou_threshold).sum(dim=0) == 0,
            (IoU > getattr(settings, self.name).iou_threshold).sum(dim=1) == 0,
            (IoU_hidden > getattr(settings, self.name).iou_threshold).sum(dim=0) == 0,
            np.array(fn_scores_ind_pred),
            gt,
            result,
            result_hidden
        )

    def get_undetected_ground_truth_index_clean(self, result, gt):

        if self.type == "Segmentation":
            raise NotImplementedError
        args = getattr(settings, self.name)
        gt = deepcopy(gt)
        result = deepcopy(result)
        result_orig = deepcopy(result)
        gt = self.filter_result(gt, gt=True)
        result = self.filter_result(result, gt=False, custom_confidence_threshold=0)

        result = result["instances"]
        gt = gt["instances"]
        fn_scores_ind_pred = [False for i in range(len(result))]
        print("IN CODE: ", len(result))
        IoU = pairwise_iou(result.pred_boxes, gt.pred_boxes)
        for i in range(len(result)):
            for j in range(len(gt)):
                if result.pred_classes[i] != gt.pred_classes[j]:
                    IoU[i, j] = 0

        for i in range(len(result)):
            for j in range(len(gt)):
                if IoU[i][j] > getattr(settings, self.name).iou_threshold and result.scores[i] < args.confidence_threshold:
                    fn_scores_ind_pred[i] = True

        return (
            (IoU > getattr(settings, self.name).iou_threshold).sum(dim=0) == 0,
            (IoU > getattr(settings, self.name).iou_threshold).sum(dim=1) == 0,
            fn_scores_ind_pred,
            gt,
            result,
        )
    def get_error_confidence_distribution(self, result, gt):

        if self.type == "Segmentation":
            raise NotImplementedError

        gt = deepcopy(gt)

        gt = self.filter_result(gt, gt=True)
        result = self.filter_result(result, gt=False, confidence_check=False)
        result['instances'] = result['instances'].to('cpu')

        result = result["instances"]
        gt = gt["instances"]

        IoU = pairwise_iou(result.pred_boxes, gt.pred_boxes)
        for i in range(len(result)):
            for j in range(len(gt)):
                if result.pred_classes[i] != gt.pred_classes[j]:
                    IoU[i, j] = 0

        return (
            (IoU > getattr(settings, self.name).iou_threshold).sum(dim=0) == 0,
            (IoU > getattr(settings, self.name).iou_threshold).sum(dim=1) == 0,
            gt,
            result,
        )

    def aggregate_inference_results(self, results, args):

        if self.type == "Detection":
            return self.aggregate_inference_results_detection(results, args)
        else:
            raise NotImplementedError

    def aggregate_inference_results_detection(self, results):

        base = results[0]["instances"]

        scores = [base.scores]

        for result in results[1:]:

            result = deepcopy(result["instances"])

            if len(base) == 0 or len(result) == 0:
                continue

            IoU = pairwise_iou(result.pred_boxes, base.pred_boxes)

            for i in range(len(result)):
                for j in range(len(base)):
                    if result.pred_classes[i] != base.pred_classes[j]:
                        IoU[i, j] = 0

            val, idx = IoU.max(dim=0)

            # clear those scores where IoU is way too small
            result[idx].scores[val < getattr(settings, self.name).iou_threshold] = 0.0
            scores.append(result[idx].scores)

        scores = torch.cat([i.unsqueeze(0) for i in scores], dim=0)

        base.pred_scores = torch.tensor(scores).mean(dim=0)
        base.pred_std = torch.tensor(scores).std(dim=0)

        print(base.pred_std)

        return {"instances": base}
