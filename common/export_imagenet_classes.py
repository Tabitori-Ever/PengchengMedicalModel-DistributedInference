from torchvision.models import AlexNet_Weights

classes = AlexNet_Weights.IMAGENET1K_V1.meta["categories"]

with open("imagenet_classes.txt", "w") as f:
    for c in classes:
        f.write(c + "\n")

print("Saved imagenet_classes.txt")