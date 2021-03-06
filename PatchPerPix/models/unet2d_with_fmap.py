import tensorflow as tf
import numpy as np


def conv_pass(
        fmaps_in, *,
        kernel_size,
        num_fmaps,
        num_repetitions,
        activation,
        padding,
        name):

    """Create a convolution pass::

        f_in --> f_1 --> ... --> f_n

    where each ``-->`` is a convolution followed by a (non-linear) activation
    function and ``n`` ``num_repetitions``. Each convolution will decrease the
    size of the feature maps by ``kernel_size-1``.

    Args:

        fmaps_in:

            The input tensor of shape ``(batch_size, channels, depth, height, width)``.

        kernel_size:

            Size of the kernel. Forwarded to tf.layers.conv2d.

        num_fmaps:

            The number of feature maps to produce with each convolution.

        num_repetitions:

            How many convolutions to apply.

        activation:

            Which activation to use after a convolution. Accepts the name of any
            tensorflow activation function (e.g., ``relu`` for ``tf.nn.relu``).

        name:

            Name of tensor to log net layers.

        padding:

            Which padding should be applied to convolutions. Either 'valid' or 'same'.

    """

    fmaps = fmaps_in
    if activation is not None:
        activation = getattr(tf.nn, activation)

    for i in range(num_repetitions):
        fmaps = tf.layers.conv2d(
            inputs=fmaps,
            filters=num_fmaps,
            kernel_size=kernel_size,
            padding=padding,
            data_format='channels_first',
            activation=activation,
            name=name + '_%i'%i)

    return fmaps


def downsample(fmaps_in, factors, name='down', padding='valid'):

    fmaps = tf.layers.max_pooling2d(
        fmaps_in,
        pool_size=factors,
        strides=factors,
        padding=padding,
        data_format='channels_first',
        name=name)

    return fmaps


def upsample(fmaps_in, factors, num_fmaps, activation='relu', name='up',
             padding='valid',
             upsampling="trans_conv"):

    if activation is not None:
        activation = getattr(tf.nn, activation)

    if upsampling == "resize_conv":
        fmaps_in = tf.keras.backend.resize_images(
            fmaps_in,
            factors[0],
            factors[1],
            "channels_first")

        fmaps = tf.layers.conv2d(
            inputs=fmaps_in,
            filters=num_fmaps,
            kernel_size=factors,
            padding=padding,
            data_format='channels_first',
            activation=activation,
            name=name)
    elif upsampling == "trans_conv":
        fmaps = tf.layers.conv2d_transpose(
            fmaps_in,
            filters=num_fmaps,
            kernel_size=factors,
            strides=factors,
            padding=padding,
            data_format='channels_first',
            activation=activation,
            name=name)
    else:
        raise ValueError("invalid value for upsampling method", upsampling)

    return fmaps


def crop_spatial(fmaps_in, shape):
    '''Crop only the spacial dimensions to match shape.

    Args:

        fmaps_in:

            The input tensor.

        shape:

            A list (not a tensor) with the requested shape [_, _, z, y, x] or
            [_, _, y, x].
    '''

    in_shape = fmaps_in.get_shape().as_list()

    offset = [0, 0] + [(in_shape[i] - shape[i]) // 2 for i in range(2, len(shape))]
    size = in_shape[0:2] + shape[2:]

    fmaps = tf.slice(fmaps_in, offset, size)

    return fmaps

def crop(a, shape):
    '''Crop a to a new shape, centered in a.

    Args:

        a:

            The input tensor.

        shape:

            A list (not a tensor) with the requested shape.
    '''

    in_shape = a.get_shape().as_list()

    offset = list([
        (i - s)//2
        for i, s in zip(in_shape, shape)
    ])

    b = tf.slice(a, offset, shape)

    return b


def unet_with_fmap(
        fmaps_in, *,
        low_res_fmaps_in,
        num_fmaps,
        fmap_inc_factors,
        fmap_dec_factors,
        downsample_factors,
        activation,
        padding,
        num_repetitions,
        kernel_size,
        upsampling="trans_conv",
        layer=0,
        layer_concat=3,
        num_fmaps_concat=128):

    """Create a U-Net::

        f_in --> f_left --------------------------->> f_right--> f_out
                    |                                   ^
                    v                                   |
                 g_in --> g_left ------->> g_right --> g_out
                             |               ^
                             v               |
                                   ...

    where each ``-->`` is a convolution pass (see ``conv_pass``), each `-->>` a
    crop, and down and up arrows are max-pooling and transposed convolutions,
    respectively.

    The U-Net expects tensors to have shape ``(batch=1, channels, depth, height,
    width)``.

    This U-Net performs only "valid" convolutions, i.e., sizes of the feature
    maps decrease after each convolution.

    Args:

        fmaps_in:

            The input tensor.

        num_fmaps:

            The number of feature maps in the first layer. This is also the
            number of output feature maps.

        fmap_inc_factor:

            By how much to multiply the number of feature maps between layers.
            If layer 0 has ``k`` feature maps, layer ``l`` will have
            ``k*fmap_inc_factor**l``.

        downsample_factors:

            List of lists ``[z, y, x]`` to use to down- and up-sample the
            feature maps between layers.

        activation:

            Which activation to use after a convolution. Accepts the name of any
            tensorflow activation function (e.g., ``relu`` for ``tf.nn.relu``).

        layer:

            Used internally to build the U-Net recursively.
    """

    prefix = "    " * layer
    print(prefix + "Creating U-Net layer %i" % layer)
    print(prefix + "f_in: " + str(fmaps_in.shape))

    # create upsampling part of low resolution fmaps
    if layer == layer_concat:

        print(prefix + "upsampling fmaps branch")

        low_res_branch = []
        num_low_res_fmaps = len(low_res_fmaps_in)
        for i in range(num_low_res_fmaps):

            low_res_branch += (conv_pass(low_res_fmaps_in[i],
                                         kernel_size=kernel_size,
                                         num_fmaps=num_fmaps_concat,
                                         num_repetitions=num_repetitions,
                                         activation=activation,
                                         padding='valid',
                                         name='low_res_%i' % i),
                               )

            print("low_res: {}".format(low_res_branch[-1].shape))

        # upsampling without convolutions in between
        for i in range(num_low_res_fmaps):

            low_res_branch[i] = upsample(
                low_res_branch[i],
                [2, 2],
                num_fmaps=int(num_fmaps_concat/2 * (i + 1)),
                activation=activation,
                name='low_res_up_%i' % i,
                upsampling=upsampling,
                padding=padding)

            print("low_res_up: {} {}".format(i, low_res_branch[i].shape))

            if i < num_low_res_fmaps - 1:

                # crop upsampled volume to meet upper fmap size
                low_res_branch[i] = crop_spatial(
                    low_res_branch[i],
                    low_res_branch[i+1].get_shape().as_list())
                print("low_res_cropped: {} {}".format(i, low_res_branch[i].shape))

                low_res_branch[i + 1] = tf.concat([low_res_branch[i + 1],
                                                   low_res_branch[i]], 1)

                print("low_res_concat: {}/{} {}".format(
                    i, i+1, low_res_branch[i + 1].shape))

    # convolve
    f_left = conv_pass(
        fmaps_in,
        kernel_size=kernel_size,
        num_fmaps=num_fmaps,
        num_repetitions=num_repetitions,
        activation=activation,
        padding=padding,
        name='unet_layer_%i_left' % layer)

    # concat with additional input fmaps
    if layer == layer_concat:
        # crop fmaps
        low_res_branch[-1] = crop_spatial(low_res_branch[-1],
                                          f_left.get_shape().as_list())
        f_left = tf.concat([f_left, low_res_branch[-1]], 1)
        print("concat fmaps and f_left: " + str(f_left.shape))

        num_fmaps_up = num_fmaps + int(num_fmaps_concat / 2 * 5)

        f_left = conv_pass(
            f_left,
            kernel_size=kernel_size,
            num_fmaps=num_fmaps_up,
            num_repetitions=num_repetitions,
            activation=activation,
            padding='valid',
            name='unet_layer_%i_left_after_concat' % layer)

    # last layer does not recurse
    bottom_layer = (layer == len(downsample_factors))
    if bottom_layer:
        print(prefix + "bottom layer")
        print(prefix + "f_out: " + str(f_left.shape))
        return f_left

    # downsample
    g_in = downsample(
        f_left,
        downsample_factors[layer],
        'unet_down_%i_to_%i' % (layer, layer + 1),
        padding=padding)

    # recursive U-net
    g_out = unet_with_fmap(
        g_in,
        low_res_fmaps_in=low_res_fmaps_in,
        num_fmaps=num_fmaps*fmap_inc_factors[layer],
        fmap_inc_factors=fmap_inc_factors,
        fmap_dec_factors=fmap_dec_factors,
        downsample_factors=downsample_factors,
        activation=activation,
        padding=padding,
        kernel_size=kernel_size,
        num_repetitions=num_repetitions,
        upsampling=upsampling,
        layer=layer+1,
        layer_concat=layer_concat,
        num_fmaps_concat=num_fmaps_concat,
    )

    print(prefix + "g_out: " + str(g_out.shape))

    num_fmaps_up = num_fmaps*np.prod(fmap_inc_factors[layer:])/np.prod(fmap_dec_factors[layer:]) \
                   + int(num_fmaps_concat/2 * 5)
    #num_fmaps_up = num_fmaps + int(num_fmaps_concat/2 * 5)
    #num_fmaps_up = 720

    # upsample
    g_out_upsampled = upsample(
        g_out,
        downsample_factors[layer],
        num_fmaps=num_fmaps_up,
        activation=activation,
        name='unet_up_%i_to_%i'%(layer + 1, layer),
        upsampling=upsampling,
        padding=padding)

    print(prefix + "g_out_upsampled: " + str(g_out_upsampled.shape))

    # copy-crop
    f_left_cropped = crop_spatial(f_left,
                                  g_out_upsampled.get_shape().as_list())

    print(prefix + "f_left_cropped: " + str(f_left_cropped.shape))

    # concatenate along channel dimension
    f_right = tf.concat([f_left_cropped, g_out_upsampled], 1)

    print(prefix + "f_right: " + str(f_right.shape))

    # convolve
    f_out = conv_pass(
        f_right,
        kernel_size=kernel_size,
        num_fmaps=num_fmaps_up,
        num_repetitions=num_repetitions,
        activation=activation,
        padding=padding,
        name='unet_layer_%i_right' % layer)

    print(prefix + "f_out: " + str(f_out.shape))

    return f_out


if __name__ == "__main__":

    # test
    raw = tf.placeholder(tf.float32, shape=(1, 1, 268, 268))

    model = unet(raw, 12, 5, [[3,3],[3,3],[3,3]])
    tf.train.export_meta_graph(filename='unet.meta')

    with tf.Session() as session:
        session.run(tf.initialize_all_variables())
        tf.summary.FileWriter('.', graph=tf.get_default_graph())
        # writer = tf.train.SummaryWriter(logs_path, graph=tf.get_default_graph())


    print(model.shape)
