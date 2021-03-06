import numpy as np
import skimage.io
import tensorflow as tf

from network_helpers import conv2d, spatial_batch_norm, load_image
from vgg_network import VGGNetwork

def leaky_relu(input_layer, alpha):
    return tf.maximum(tf.mul(input_layer, alpha), input_layer)


def input_pyramid(name, M, batch_size, k=5):
    """
    Generates k inputs at different scales, with MxM being the largest.
    """
    with tf.get_default_graph().name_scope(name):
        return_val = [tf.placeholder(tf.float32, [batch_size, M//(2**x), M//(2**x), 3], name=str(x)) for x in range(k)]
        return_val.reverse()
    return return_val


def noise_pyramid(M, batch_size, k=5):
    return [np.random.rand(batch_size, M//(2**x), M//(2**x), 3) for x in range(k)][::-1]


def conv_block(name, input_layer, kernel_size, out_channels):
    """
    Per Ulyanov et el, this is a block consisting of
        - Mirror pad (TODO)
        - Number of maps from a convolutional layer equal to out_channels (multiples of 8)
        - Spatial BatchNorm
        - LeakyReLu
    """
    with tf.get_default_graph().name_scope(name):
        in_channels = input_layer.get_shape().as_list()[-1]

        # Xavier initialization, http://jmlr.org/proceedings/papers/v9/glorot10a/glorot10a.pdf
        # The application of this method here seems unorthodox since we're using ReLU, not sigmoid or tanh.
        low = -np.sqrt(6.0/(in_channels + out_channels))
        high = np.sqrt(6.0/(in_channels + out_channels))
        weights = tf.Variable(tf.random_uniform([kernel_size, kernel_size, in_channels, out_channels], minval=low, maxval=high), name='weights')
        biases = tf.Variable(tf.random_uniform([out_channels], minval=low, maxval=high), name='biases')
        # TODO: Mirror pad the conv2d? I'm not sure how important this is.
        conv = conv2d(input_layer, weights, biases)
        batch_norm = spatial_batch_norm(conv)
        relu = leaky_relu(batch_norm, .01)
        return relu


def conv_chain(name, input_layer, out_channels):
    """
    A sequence of three conv_block units with 3x3, 3x3, and 1x1 kernels respectively.
    There's nothing inherently magical about this abstraction, but it's a repeated pattern in the Ulyanov et el network.
    """
    with tf.get_default_graph().name_scope(name):
        block1 = conv_block("layer1", input_layer, kernel_size=3, out_channels=out_channels)
        block2 = conv_block("layer1", block1, kernel_size=3, out_channels=out_channels)
        block3 = conv_block("layer1", block2, kernel_size=1, out_channels=out_channels)
    return block3


def join_block(name, lower_res_layer, higher_res_layer):
    """
    A block that combines two resolutions by upsampling the lower, batchnorming both, and concatting.
    """
    with tf.get_default_graph().name_scope(name):
        upsampled = tf.image.resize_nearest_neighbor(lower_res_layer, higher_res_layer.get_shape().as_list()[1:3])
        batch_norm_lower = spatial_batch_norm(upsampled, 'normLower')
        batch_norm_higher = spatial_batch_norm(higher_res_layer, 'normHigher')
    return tf.concat(3, [batch_norm_lower, batch_norm_higher])


class TextureNetwork(object):
    inputDimension = 224
    channelStepSize = 8
    batchSize = 1  # 16 in the paper
    epochs = 1000  # 2000 in the paper

    def __init__(self, style_img_path):
        self.graph = tf.Graph()
        with self.graph.as_default():
            """
            Construct the texture network graph structure
            """
            self.noise_inputs = input_pyramid("noise", self.inputDimension, self.batchSize)
            current_channels = 8
            current_noise_aggregate = self.noise_inputs[0]
            for noise_frame in self.noise_inputs[1:]:
                low_res_out = conv_chain("chain_lower_%d" % current_channels, current_noise_aggregate, current_channels)
                high_res_out = conv_chain("chain_higher", noise_frame, self.channelStepSize)
                current_channels += self.channelStepSize
                current_noise_aggregate = join_block("join_%d" % (current_channels + self.channelStepSize), low_res_out, high_res_out)
            final_chain = conv_chain("output_chain", current_noise_aggregate, current_channels)
            self.output = conv_block("output", final_chain, kernel_size=1, out_channels=3)

            """
            Calculate style loss by computing gramians from both the output of the texture net above and from the
            texture sample image.
            """
            self.texture_image = tf.to_float(tf.constant(load_image(style_img_path).reshape((1, 224, 224, 3))))
            #content_images = tf.placeholder("float", [self.batchSize, 224, 224, 3])
            image_vgg = VGGNetwork("image_vgg", tf.concat(0, [self.texture_image, self.output, self.output]), 1, self.batchSize, self.batchSize)

            self.loss = image_vgg.style_loss([(i, 1) for i in range(1, 6)])

    def run_train(self):
        with self.graph.as_default():
            optimizer = tf.train.AdamOptimizer(learning_rate=0.01, beta1=0.9, beta2=0.999, epsilon=1e-08, use_locking=False, name='Adam')
            train_step = optimizer.minimize(self.loss)

            """
            Train over epochs, printing loss at each one
            """
            saver = tf.train.Saver()
            with tf.Session() as sess:
                init = tf.initialize_all_variables()
                sess.run(init)
                for i in range(self.epochs):
                    feed_dict = {}
                    noise = noise_pyramid(self.inputDimension, self.batchSize)
                    for index, noise_frame in enumerate(self.noise_inputs):
                        feed_dict[noise_frame] = noise[index]
                    train_step.run(feed_dict=feed_dict)
                    # print("loss", i, sess.run(self.loss, feed_dict=feed_dict))
                    if i > 0 and i % 20 == 0:  # TODO: Make this interval an argument
                        saver.save(sess, "models/snapshot-%d.ckpt" % i)
                        network_out = sess.run(self.output, feed_dict=feed_dict).reshape((224, 224, 3))
                        img_out = np.clip(np.array(network_out) * 255.0, 0, 255).astype('uint8')
                        skimage.io.imsave("img/aa-iteration-%d.jpeg" % i, img_out)


# TODO: Add argument parsing and command-line args
t = TextureNetwork('img/style.jpg')
t.run_train()
