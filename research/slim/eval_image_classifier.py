# Copyright 2016 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Generic evaluation script that evaluates a model using a given dataset."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math
import sys
import tensorflow as tf

from datasets import dataset_factory
from nets import nets_factory
from preprocessing import preprocessing_factory
from slim_evaluation import evaluate_once

slim = tf.contrib.slim

tf.app.flags.DEFINE_integer(
    'batch_size', 50, 'The number of samples in each batch.')

tf.app.flags.DEFINE_integer(
    'max_num_batches', None,
    'Max number of batches to evaluate by default use all.')

tf.app.flags.DEFINE_string(
    'master', '', 'The address of the TensorFlow master to use.')

tf.app.flags.DEFINE_string(
    'checkpoint_path', '/tmp/tfmodel/',
    'The directory where the model was written to or an absolute path to a '
    'checkpoint file.')

tf.app.flags.DEFINE_string(
    'eval_dir', '/tmp/tfmodel/', 'Directory where the results are saved to.')

tf.app.flags.DEFINE_integer(
    'num_preprocessing_threads', 4,
    'The number of threads used to create the batches.')

tf.app.flags.DEFINE_string(
    'dataset_name', 'imagenet', 'The name of the dataset to load.')

tf.app.flags.DEFINE_string(
    'dataset_split_name', 'test', 'The name of the train/test split.')

tf.app.flags.DEFINE_string(
    'dataset_dir', None, 'The directory where the dataset files are stored.')

tf.app.flags.DEFINE_integer(
    'labels_offset', 0,
    'An offset for the labels in the dataset. This flag is primarily used to '
    'evaluate the VGG and ResNet architectures which do not use a background '
    'class for the ImageNet dataset.')

tf.app.flags.DEFINE_string(
    'model_name', 'inception_v3', 'The name of the architecture to evaluate.')

tf.app.flags.DEFINE_string(
    'preprocessing_name', None, 'The name of the preprocessing to use. If left '
    'as `None`, then the model_name flag is used.')

tf.app.flags.DEFINE_float(
    'moving_average_decay', None,
    'The decay to use for the moving average.'
    'If left as None, then moving averages are not used.')

tf.app.flags.DEFINE_integer(
    'eval_image_size', None, 'Eval image size')

# MODIFIED BY JSJASON: START
tf.app.flags.DEFINE_string(
    'device', None, 'ip:host of device')

tf.app.flags.DEFINE_string(
    'server', None, 'ip:host of server')

tf.app.flags.DEFINE_integer(
    'final_layer_on_device', None,
    'Index of the final layer to be put onto device')

tf.app.flags.DEFINE_integer(
    'prof_iter_init', 0, 'iteration number to start device-side enqueue prof')

tf.app.flags.DEFINE_integer(
    'prof_iter_period', 10, 'iteration period for device-side enqueue prof')

tf.app.flags.DEFINE_integer(
    'prof_iter_final', 100, 'iteration number to finish device-side enqueue prof')
# MODIFIED BY JSJASON: END

FLAGS = tf.app.flags.FLAGS


def main(_):
  '''
  COMMENTED OUT BY JSJASON: START
  if not FLAGS.dataset_dir:
    raise ValueError('You must supply the dataset directory with --dataset_dir')
  COMMENTED OUT BY JSJASON: END
  '''

  # ADDED BY JSJASON - quick check for a required command line argument
  if FLAGS.final_layer_on_device is None:
    raise ValueError('You must supply an integer for final_layer_on_device')

  tf.logging.set_verbosity(tf.logging.INFO)
  with tf.Graph().as_default():
    tf_global_step = slim.get_or_create_global_step()

    ######################
    # Select the dataset #
    ######################
    # MODIFIED BY JSJASON: START
    # assume imagenet-data/validation-0~9 are present in /home/pi of device
    file_pattern = [('/home/pi/imagenet-data/validation-%05d-of-00128' % i) for i in range(10)]
    dataset = dataset_factory.get_dataset(
        FLAGS.dataset_name, FLAGS.dataset_split_name, FLAGS.dataset_dir, file_pattern=file_pattern)
    # MODIFIED BY JSJASON: END

    ####################
    # Select the model #
    ####################
    network_fn = nets_factory.get_network_fn(
        FLAGS.model_name,
        num_classes=(dataset.num_classes - FLAGS.labels_offset),
        is_training=False)

    ##############################################################
    # Create a dataset provider that loads data from the dataset #
    ##############################################################
    with tf.device('/job:device'): # ADDED BY JSJASON - data should be generated from device
      provider = slim.dataset_data_provider.DatasetDataProvider(
          dataset,
          shuffle=False,
          common_queue_capacity=FLAGS.batch_size,
          common_queue_min=FLAGS.batch_size)
      [image, label] = provider.get(['image', 'label'])
      label -= FLAGS.labels_offset

    #####################################
    # Select the preprocessing function #
    #####################################
      preprocessing_name = FLAGS.preprocessing_name or FLAGS.model_name
      image_preprocessing_fn = preprocessing_factory.get_preprocessing(
          preprocessing_name,
          is_training=False)

      eval_image_size = FLAGS.eval_image_size or network_fn.default_image_size

      image = image_preprocessing_fn(image, eval_image_size, eval_image_size)

      images, labels = tf.train.batch(
          [image, label],
          batch_size=FLAGS.batch_size,
          num_threads=1,
          capacity=FLAGS.batch_size)

    ####################
    # Define the model #
    ####################
    with tf.device('/job:server'): # ADDED BY JSJASON - all ops will be placed on server, unless otherwise specified
      logits, _ = network_fn(images, final_layer_on_device=FLAGS.final_layer_on_device) # MODIFIED BY JSJASON - pass additional argument

      # sys.exit(0)

      if FLAGS.moving_average_decay:
	variable_averages = tf.train.ExponentialMovingAverage(
	    FLAGS.moving_average_decay, tf_global_step)
	variables_to_restore = variable_averages.variables_to_restore(
	    slim.get_model_variables())
	variables_to_restore[tf_global_step.op.name] = tf_global_step
      else:
	variables_to_restore = slim.get_variables_to_restore()

      predictions = tf.argmax(logits, 1)
      # FIXED BY JSJASON - bug when batch_size=1
      # labels = tf.squeeze(labels)

      # Define the metrics:
      names_to_values, names_to_updates = slim.metrics.aggregate_metric_map({
	  'Accuracy': slim.metrics.streaming_accuracy(predictions, labels),
	  'Recall_5': slim.metrics.streaming_recall_at_k(
	      logits, labels, 5),
      })

      # Print the summaries to screen.
      for name, value in names_to_values.items():
	summary_name = 'eval/%s' % name
	op = tf.summary.scalar(summary_name, value, collections=[])
	op = tf.Print(op, [value], summary_name)
	tf.add_to_collection(tf.GraphKeys.SUMMARIES, op)

    # TODO(sguada) use num_epochs=1
    if FLAGS.max_num_batches:
      num_batches = FLAGS.max_num_batches
    else:
      # This ensures that we make a single pass over all of the data.
      num_batches = math.ceil(dataset.num_samples / float(FLAGS.batch_size))

    if tf.gfile.IsDirectory(FLAGS.checkpoint_path):
      checkpoint_path = tf.train.latest_checkpoint(FLAGS.checkpoint_path)
    else:
      checkpoint_path = FLAGS.checkpoint_path


    qs = tf.get_collection('SPL_queue_size')
    print('Queue size op: ' + str(qs))

    # ADDED BY JSJASON: START
    cluster_map = {
      'server': [FLAGS.server],
      'device': [FLAGS.device],
    }

    cluster = tf.train.ClusterSpec(cluster_map)
    server = tf.train.Server(cluster, job_name='server')
    # ADDED BY JSJASON: END


    tf.logging.info('Evaluating %s' % checkpoint_path)
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True

    evaluate_once(
        master=server.target, # MODIFIED BY JSJASON
        checkpoint_path=checkpoint_path,
        logdir=FLAGS.eval_dir,
        num_evals=num_batches,
        # eval_op=list(names_to_updates.values()) + qs,
        eval_op=[predictions] + qs,
        variables_to_restore=variables_to_restore,
        session_config=config)


if __name__ == '__main__':
  tf.app.run()
