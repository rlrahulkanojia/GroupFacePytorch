import sys, os
import torch, cv2
import numpy as np
from models.group_face import GroupFace
from loss.arcface_loss import ArcFaceLoss
from system.data_loader import IDDataSet, torch_loader
from system.system_funcs import visualise
from system.cos_lr import adjust_learning_rate

from sklearn.metrics.pairwise import cosine_similarity


def load_gallery(model, gallery_path):
    gallery_files = []
    gallery_ids = []
    gallery_feats = {}
    gallery_len = len(os.listdir(gallery_path))
    for i, dir in enumerate(os.listdir(gallery_path)):
        for file in os.listdir(os.path.join(gallery_path, dir)):
            file_path = os.path.join(gallery_path, dir, file)
            gallery_files.append(file_path)
            gallery_ids.append(dir)

            group_inter, final, group_prob, group_label = model(torch_loader(cv2.imread(file_path)).unsqueeze(0))
            feat = final / torch.norm(final, p=2, keepdim=False)
            feat = feat.detach().cpu().reshape(1, 256).numpy()

            if isinstance(gallery_feats, dict) is True:
                gallery_feats = feat
            else:
                gallery_feats = np.concatenate((gallery_feats, feat), 0)
            pass
        sys.stdout.write(
            "\r>> LoadGallery[{}/{}] ".format(i, gallery_len))
        sys.stdout.flush()

    print("\n")
    return gallery_files, gallery_ids, gallery_feats


def eval(model, gallery_path, probe_root_path, epoch):
    model.eval()
    gallery_files, gallery_ids, gallery_feats = load_gallery(model, gallery_path)

    whole_cnt = 0.0
    for dir in os.listdir(probe_root_path):
        for file in os.listdir(os.path.join(probe_root_path, dir)):
            whole_cnt += 1.0

    evaled_cnt = 0.0
    right_cnt = 0.0
    sum_right_top1_score = 0.0
    for dir in os.listdir(probe_root_path):
        for file in os.listdir(os.path.join(probe_root_path, dir)):
            evaled_cnt += 1
            file_path = os.path.join(probe_root_path, dir, file)
            GT_id = dir

            group_inter, final, group_prob, group_label = model(torch_loader(cv2.imread(file_path)).unsqueeze(0))
            feat = final / torch.norm(final, p=2, keepdim=False)
            feat = feat.detach().cpu().reshape(1, 256).numpy()

            scores = cosine_similarity(feat, gallery_feats)
            max_idx = np.argmax(scores)
            predicted_id = gallery_ids[max_idx]
            if predicted_id == GT_id:
                right_cnt += 1.0
                sum_right_top1_score += scores[0][max_idx]

            # visualise(file_path, gallery_files[max_idx], predicted_id, GT_id, scores[0][max_idx], timeElapse=500)

            sys.stdout.write(
                "\r EpochEval[{}] >> {}/{} acc:{}/{}={:.4f} avgRscore: {:.2f}".format(epoch, evaled_cnt, whole_cnt,
                                                                                      right_cnt, evaled_cnt,
                                                                                      right_cnt / evaled_cnt,
                                                                                      sum_right_top1_score / float(
                                                                                          right_cnt + 1e-10)))
            sys.stdout.flush()

    print("\n")
    return right_cnt / evaled_cnt


def train(model, epoch):
    adjust_learning_rate(optimizer, epoch, learning_rate,
                         float(learning_rate) / 100.0)
    for param_group in optimizer.param_groups:
        print("epoch: {} lr set to : {}".format(epoch, param_group['lr']))
    model.train()
    batch_len = len(train_data_loader)
    loss_sum = 0.0
    for i, (img, file_path, id, label) in enumerate(train_data_loader):
        img = img.cuda()
        label = label.cuda()
        group_inter, final, group_prob, group_label = model(img)
        loss = criteria_arc(final, label)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        optimizer_center.step()

        loss_sum += float(loss)
        print("Epoch: {} [{}/{}] loss:{:.2f}".format(epoch, i, batch_len, float(loss)))

    print("Epoch {} Trained Loss_Sum:{:.5f}".format(epoch, loss_sum / float(batch_len)))


if __name__ == '__main__':

    epoch_whole = 400
    learning_rate = 1e-4
    # gallery_path = "./demo_eval/gallery/"
    # probe_root_path = "./demo_eval/probe/"
    # train_root_path = "./demo_ims/"
    gallery_path = "/home/leo/samba107/lpy/dataset/MS1M/imgs_split/gallery/"
    probe_root_path = "/home/leo/samba107/lpy/dataset/MS1M/imgs_split/probe/"
    train_root_path = "/home/leo/samba107/lpy/dataset/MS1M/imgs_split/train/"

    batch_size = 4

    # pretrained_model = "choosed/Epoch_35_acc_1.00.pth"
    # pretrained_centers = "choosed/Epoch_35_centers.pth"
    pretrained_model = ""
    pretrained_centers = ""
    save_checkpoints_every_epoch = False

    checkpoints_save_path = "checkpoints"
    if os.path.exists(checkpoints_save_path) is False:
        os.makedirs(checkpoints_save_path)

    train_dataset = IDDataSet(train_root_path, "dataset.pk")
    train_data_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=0)

    model = GroupFace(resnet=50)
    if os.path.exists(pretrained_model) is True:
        print("loading weights {}".format(pretrained_model))
        sys.stdout.flush()
        model.load_state_dict(torch.load(pretrained_model))

    if torch.cuda.is_available():
        model = torch.nn.DataParallel(model).cuda()

    criteria_arc = ArcFaceLoss(num_classes=len(train_dataset.IDs), m=0.5, partial_fc_rate=0.1)
    # criteria_arc = ArcFaceLoss(num_classes=10, m=0.5, partial_fc_rate=1)
    if os.path.exists(pretrained_centers) is True:
        print("loading centers {}".format(pretrained_centers))
        sys.stdout.flush()
        criteria_arc.weight.load_state_dict(torch.load(pretrained_centers))

    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    optimizer_center = torch.optim.Adam(criteria_arc.weight.parameters(), lr=learning_rate)
    # criteria_arc = torch.nn.CrossEntropyLoss()

    eval(model, gallery_path, probe_root_path, -1)
    print("     \n START Training")
    for epoch in range(epoch_whole):
        train(model, epoch)
        acc = eval(model, gallery_path, probe_root_path, epoch)
        if save_checkpoints_every_epoch is True:
            out_path = os.path.join(checkpoints_save_path, "Epoch_{}_acc_{:.2f}.pth".format(epoch, acc))
            torch.save(model.module.state_dict(), out_path)
            out_path_centers = os.path.join(checkpoints_save_path, "Epoch_{}_centers.pth".format(epoch))
            torch.save(criteria_arc.weight.state_dict(), out_path_centers)

    out_path = os.path.join(checkpoints_save_path, "Epoch_Final_acc_{:.2f}.pth".format(acc))
    torch.save(model.module.state_dict(), out_path)
    out_path_centers = os.path.join(checkpoints_save_path, "Epoch_Final_centers.pth")
    torch.save(criteria_arc.weight.state_dict(), out_path_centers)
    pass
