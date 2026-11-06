# nao_description

The NAO V40 robot model (URDF) and an RViz bringup, vendored from Kenji Brameld's
`nao_description` package (Apache 2.0; the URDF is BSD, see `NOTICE`).

## Setup

To install the 3d model meshes for the NAO, in this nao_description package directory,

    mkdir tmp && cd tmp
    wget https://github.com/ros-naoqi/nao_meshes_installer/raw/master/naomeshes-0.6.7-linux-x64-installer.run
    chmod +x naomeshes-0.6.7-linux-x64-installer.run && ./naomeshes-0.6.7-linux-x64-installer.run  --prefix .
    *Follow instructions, don't change the install directory*
    mv -t ../  meshes/ texture/
    cd ../ && rm -rf tmp/

## Running

To visualize the urdf robot in rviz, run

`ros2 launch nao_description everything_launch.py`

## Environment

The upstream package was tested with ROS 2 Foxy on Ubuntu 20.04.

## Using in another launch file

    file_path = os.path.join(
        get_package_share_directory('nao_description'),
            'urdf',
            'nao.urdf')
    with open(file_path, 'r') as infp:
        robot_description = infp.read()
