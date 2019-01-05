import torch
import torchvision.transforms as transforms
import torch.nn.functional as F
from datetime import datetime
import matplotlib.pyplot as plt
plt.switch_backend('agg')
import numpy as np
from verification import evaluate
import io


def l2_norm(input,axis=1):
    norm = torch.norm(input,2,axis,True)
    output = torch.div(input, norm)
    return output


def make_weights_for_balanced_classes(images, nclasses):
    '''
        Make a vector of weights for each image in the dataset, based
        on class frequency. The returned vector of weights can be used
        to create a WeightedRandomSampler for a DataLoader to have
        class balancing when sampling for a training batch.
            images - torchvisionDataset.imgs
            nclasses - len(torchvisionDataset.classes)
        https://discuss.pytorch.org/t/balanced-sampling-between-classes-with-torchvision-dataloader/2703/3
    '''
    count = [0] * nclasses
    for item in images:
        count[item[1]] += 1  # item is (img-data, label-id)
    weight_per_class = [0.] * nclasses
    N = float(sum(count))  # total number of images
    for i in range(nclasses):
        weight_per_class[i] = N / float(count[i])
    weight = [0] * len(images)
    for idx, val in enumerate(images):
        weight[idx] = weight_per_class[val[1]]
    return weight


def separate_irse_bn_paras(modules):
    if not isinstance(modules, list):
        modules = [*modules.modules()]
    paras_only_bn = []
    paras_wo_bn = []
    for layer in modules:
        if 'model' in str(layer.__class__):
            continue
        if 'container' in str(layer.__class__):
            continue
        else:
            if 'batchnorm' in str(layer.__class__):
                paras_only_bn.extend([*layer.parameters()])
            else:
                paras_wo_bn.extend([*layer.parameters()])
    return paras_only_bn, paras_wo_bn


def de_preprocess(tensor):
    return tensor*0.5 + 0.5


hflip = transforms.Compose([
            de_preprocess,
            transforms.ToPILImage(),
            transforms.functional.hflip,
            transforms.ToTensor(),
            transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5])
        ])


def hflip_batch(imgs_tensor):
    hfliped_imgs = torch.empty_like(imgs_tensor)
    for i, img_ten in enumerate(imgs_tensor):
        hfliped_imgs[i] = hflip(img_ten)
    return hfliped_imgs


def get_time():
    return (str(datetime.now())[:-10]).replace(' ','-').replace(':','-')


def gen_plot(fpr, tpr):
    """Create a pyplot plot and save to buffer."""
    plt.figure()
    plt.xlabel("FPR", fontsize=14)
    plt.ylabel("TPR", fontsize=14)
    plt.title("ROC Curve", fontsize=14)
    plot = plt.plot(fpr, tpr, linewidth=2)
    buf = io.BytesIO()
    plt.savefig(buf, format='jpeg')
    buf.seek(0)
    plt.close()
    return buf

def perform_validation(multi_gpu,device,embedding_size,batch_size,backbone,carray,issame,nrof_folds=5,tta=False):
    if multi_gpu:
        backbone = backbone.module # unpackage model from DataParallel
    else:
        backbone = backbone
    backbone.eval().to(device)
    idx = 0
    embeddings = np.zeros([len(carray), embedding_size])
    with torch.no_grad():
        while idx + batch_size <= len(carray):
            batch = torch.tensor(carray[idx:idx + batch_size])
            if tta:
                fliped = hflip_batch(batch)
                emb_batch = backbone(batch.to(device)) + backbone(fliped.to(device))
                embeddings[idx:idx + batch_size] = F.normalize(emb_batch)
            else:
                embeddings[idx:idx + batch_size] = F.normalize(backbone(batch.to(device)).cpu())
            idx += batch_size
        if idx < len(carray):
            batch = torch.tensor(carray[idx:])
            if tta:
                fliped = hflip_batch(batch)
                emb_batch = backbone(batch.to(device)) + backbone(fliped.to(device))
                embeddings[idx:] = F.normalize(emb_batch)
            else:
                embeddings[idx:] = F.normalize(backbone(batch.to(device)).cpu())
    tpr, fpr, accuracy, best_thresholds = evaluate(embeddings, issame, nrof_folds)
    buf = gen_plot(fpr, tpr)
    roc_curve = Image.open(buf)
    roc_curve_tensor = transforms.ToTensor()(roc_curve)
    return accuracy.mean(), best_thresholds.mean(), roc_curve_tensor


def buffer_val(writer, db_name, acc, best_threshold, roc_curve_tensor, epoch):
    writer.add_scalar('{}_Accuracy'.format(db_name), acc, epoch)
    writer.add_scalar('{}_Best_Threshold'.format(db_name), acc, epoch)
    writer.add_image('{}_ROC_Curve'.format(db_name), roc_curve_tensor, epoch)


def schedule_lr(optimizer):
        for params in optimizer.param_groups:                 
            params['lr'] /= 10.
        print(optimizer)