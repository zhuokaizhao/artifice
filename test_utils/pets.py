"""Create tfrecord files from the pets dataset.

For the sake of this example, we ignore the different breeds of dogs and cats,
instead mapping every example to one of two classes, either dog or cat. We
consider the boundary-region of each trimap to be part of the background, for
simplicity.

So our class mapping is:
{ 0 : 'background',
  1 : 'dog',
  2 : 'cat' }

But the original trimaps have the following mapping:
{ 1 : 'animal',
  2 : 'background',
  3 : 'edge' }

Note: some of this implementation is based on
https://github.com/tensorflow/models/blob/master/research/object_detection/dataset_tools/create_pet_tf_record.py,
which deals with this dataset directly.

In this script, we produce increasing levels of data scarcity, as well as simple
augmentation methods, per image. These are controlled via the command line.

Should be run from $ARTIFICE, as always.

"""

import re
import os
import numpy as np
import tensorflow as tf
from glob import glob
from artifice.utils import img
import matplotlib.pyplot as plt
import logging

logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)

# Class names to class mappings (capital letters are cats)
breeds = {
  'Abyssinian' : 2,
  'Bengal' : 2,
  'Birman' : 2,
  'Bombay' : 2,
  'British_Shorthair' : 2,
  'Egyptian_Mau' : 2,
  'Maine_Coon' : 2,
  'Persian' : 2,
  'Ragdoll' : 2,
  'Russian_Blue' : 2,
  'Siamese' : 2,
  'Sphynx' : 2,
  'american_bulldog' : 1,
  'american_pit_bull_terrier' : 1,
  'basset_hound' : 1,
  'beagle' : 1,
  'boxer' : 1,
  'chihuahua' : 1,
  'english_cocker_spaniel' : 1,
  'english_setter' : 1,
  'german_shorthaired' : 1,
  'great_pyrenees' : 1,
  'havanese' : 1,
  'japanese_chin' : 1,
  'keeshond' : 1,
  'leonberger' : 1,
  'miniature_pinscher' : 1,
  'newfoundland' : 1,
  'pomeranian' : 1,
  'pug' : 1,
  'saint_bernard' : 1,
  'samoyed' : 1,
  'scottish_terrier' : 1,
  'shiba_inu' : 1,
  'staffordshire_bull_terrier' : 1,
  'wheaten_terrier' : 1,
  'yorkshire_terrier' : 1
}


def class_from_filename(file_name):
  """Gets the integer class label from a file name.
  """
  match = re.match(r'([A-Za-z_]+)(_[0-9]+\.(png)|(jpg))', file_name, re.I)
  return classes[match.groups()[0]]


def create_dataset(fname,
                   images_path='data/pets/images',
                   annotations_path='data/pets/annotations/trimaps'):
  
  """
  :fname: tfrecord file to write to
  """

  writer = tf.python_io.TFRecordWriter(fname)

  image_names = sorted(glob(os.path.join(images_path, "*.jpg")))
  annotation_names = sorted(glob(os.path.join(annotations_path, "*.png")))
  N = len(image_names)
  
  for i in range(N):
    logging.info("Rendering scene {} of {}.".format(i, N))

    image = img.open_as_array(image_names[i])
    annotation = img.open_as_array(annotation_names[i])
    label = class_from_filename(os.path.basename(annotation_names[i]))
    annotation[annotation == 1] = label # animal
    annotation[annotation == 2] = 0     # background
    annotation[annotation == 3] = 0     # boundary -> background

    # debug:
    plt.figure()
    plt.imshow(image)
    plt.figure()
    plt.imshow(annotation)
    plt.show()

    e = dataset.example_string_from_scene(image, annotation)
    writer.write(e)
    break
  
  writer.close()
  
    
def main():
  create_dataset("data/pets/pets.tfrecord")
  

if __name__ == "__main__":
  main()
