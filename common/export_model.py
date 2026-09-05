import torch

from torchvision.models import alexnet
from torchvision.models import AlexNet_Weights

from model import AlexNetPart1
from model import AlexNetPart2


def main():

    print("=" * 50)
    print("Downloading pretrained AlexNet...")
    print("=" * 50)

    full_model = alexnet(
        weights=AlexNet_Weights.IMAGENET1K_V1
    )

    full_model.eval()

    print(full_model)

    print("=" * 50)
    print("Splitting model...")
    print("=" * 50)

    part1 = AlexNetPart1(full_model)

    part2 = AlexNetPart2(full_model)

    torch.save(part1.state_dict(), "part1.pt")

    torch.save(part2.state_dict(), "part2.pt")

    print("Saved part1.pt")

    print("Saved part2.pt")


if __name__ == "__main__":

    main()