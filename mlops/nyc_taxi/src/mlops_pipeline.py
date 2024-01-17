"""
This module defines a machine learning pipeline for processing, training, and evaluating data.

The pipeline executes the following steps in order:
1. Prepare Sample Data: Preprocesses raw data to make it suitable for further processing and analysis.
2. Transform Sample Data: Performs advanced data transformations such as feature engineering.
3. Train with Sample Data: Trains a machine learning model using the transformed data.
4. Predict with Sample Data: Uses the trained model to make predictions on new data.
5. Score with Sample Data: Evaluates the model's performance based on its predictions.
6. Finalize and Persist Model: Handles tasks like persisting model metadata, registering the model,
and generating reports.
"""
from azure.identity import DefaultAzureCredential
import argparse
from azure.ai.ml.dsl import pipeline
from azure.ai.ml import MLClient, Input
from azure.ai.ml import load_component
import time
import os
import json
from mlops.common.get_compute import get_compute
from mlops.common.get_environment import get_environment


gl_pipeline_components = []


@pipeline()
def nyc_taxi_data_regression(pipeline_job_input, model_name, build_reference):
    """
    Runs a pipeline for regression analysis on NYC taxi data.

    Parameters:
    pipeline_job_input (str): Path to the input data.
    model_name (str): Name of the model.
    build_reference (str): Reference for the build.

    Returns:
    dict: A dictionary containing paths to the prepped data, transformed data, trained model, test data, predictions, and score report.
    """    
    prepare_sample_data = gl_pipeline_components[0](
        raw_data=pipeline_job_input,
    )
    transform_sample_data = gl_pipeline_components[1](
        clean_data=prepare_sample_data.outputs.prep_data,
    )
    train_with_sample_data = gl_pipeline_components[2](
        training_data=transform_sample_data.outputs.transformed_data,
    )
    predict_with_sample_data = gl_pipeline_components[3](
        model_input=train_with_sample_data.outputs.model_output,
        test_data=train_with_sample_data.outputs.test_data,
    )
    score_with_sample_data = gl_pipeline_components[4](
        predictions=predict_with_sample_data.outputs.predictions,
        model=train_with_sample_data.outputs.model_output,
    )
    gl_pipeline_components[5](
        model_metadata=train_with_sample_data.outputs.model_metadata,
        model_name=model_name,
        score_report=score_with_sample_data.outputs.score_report,
        build_reference=build_reference,
    )

    return {
        "pipeline_job_prepped_data": prepare_sample_data.outputs.prep_data,
        "pipeline_job_transformed_data": transform_sample_data.outputs.transformed_data,
        "pipeline_job_trained_model": train_with_sample_data.outputs.model_output,
        "pipeline_job_test_data": train_with_sample_data.outputs.test_data,
        "pipeline_job_predictions": predict_with_sample_data.outputs.predictions,
        "pipeline_job_score_report": score_with_sample_data.outputs.score_report,
    }


def construct_pipeline(
    cluster_name: str,
    environment_name: str,
    display_name: str,
    deploy_environment: str,
    build_reference: str,
    model_name: str,
    data_config_path: str,
    ml_client
):
    """
    Constructs a pipeline job for NYC taxi data regression.

    Args:
        cluster_name (str): The name of the cluster to use for pipeline execution.
        environment_name (str): The name of the environment to use for pipeline execution.
        display_name (str): The display name of the pipeline job.
        deploy_environment (str): The environment to deploy the pipeline job.
        build_reference (str): The build reference for the pipeline job.
        model_name (str): The name of the model.
        data_config_path (str): The path to the data configuration file.
        ml_client: The machine learning client.

    Returns:
        pipeline_job: The constructed pipeline job.
    """    
    dataset_name = None
    config_file = open(data_config_path)
    data_config = json.load(config_file)
    for elem in data_config['datasets']:
        if 'DATA_PURPOSE' in elem and 'ENV_NAME' in elem:
            if deploy_environment == elem['ENV_NAME']:
                dataset_name = elem["DATASET_NAME"]

    registered_data_asset = ml_client.data.get(name=dataset_name, label='latest')
    parent_dir = os.path.join(os.getcwd(), "mlops/nyc_taxi/components")

    prepare_data = load_component(source=parent_dir + "/prep.yml")
    transform_data = load_component(source=parent_dir + "/transform.yml")
    train_model = load_component(source=parent_dir + "/train.yml")
    predict_result = load_component(source=parent_dir + "/predict.yml")
    score_data = load_component(source=parent_dir + "/score.yml")
    register_model = load_component(source=parent_dir + "/register.yml")

    # Set the environment name to custom environment using name and version number
    prepare_data.environment = environment_name
    transform_data.environment = environment_name
    train_model.environment = environment_name
    predict_result.environment = environment_name
    score_data.environment = environment_name
    register_model.environment = environment_name

    gl_pipeline_components.append(prepare_data)
    gl_pipeline_components.append(transform_data)
    gl_pipeline_components.append(train_model)
    gl_pipeline_components.append(predict_result)
    gl_pipeline_components.append(score_data)
    gl_pipeline_components.append(register_model)

    pipeline_job = nyc_taxi_data_regression(
        Input(type="uri_folder", path=registered_data_asset.id), model_name, build_reference
    )
    pipeline_job.display_name = display_name
    pipeline_job.tags = {
        "environment": deploy_environment,
        "build_reference": build_reference,
    }

    # demo how to change pipeline output settings
    pipeline_job.outputs.pipeline_job_prepped_data.mode = "rw_mount"

    # set pipeline level compute
    pipeline_job.settings.default_compute = cluster_name
    pipeline_job.settings.force_rerun = True
    # set pipeline level datastore
    pipeline_job.settings.default_datastore = "workspaceblobstore"

    return pipeline_job


def execute_pipeline(
    subscription_id: str,
    resource_group_name: str,
    workspace_name: str,
    experiment_name: str,
    pipeline_job: pipeline,
    wait_for_completion: str,
    output_file: str,
):
    """
    Executes a pipeline job in Azure Machine Learning service.

    Args:
        subscription_id (str): The Azure subscription ID.
        resource_group_name (str): The name of the resource group.
        workspace_name (str): The name of the Azure Machine Learning workspace.
        experiment_name (str): The name of the experiment.
        pipeline_job (pipeline): The pipeline job to be executed.
        wait_for_completion (str): Indicates whether to wait for the job to complete. Valid values are "True" or "False".
        output_file (str): The path to the output file where the job name will be written.

    Raises:
        Exception: If the job fails to complete.

    Returns:
        None
    """    
    try:
        client = MLClient(
            DefaultAzureCredential(),
            subscription_id=subscription_id,
            resource_group_name=resource_group_name,
            workspace_name=workspace_name,
        )

        pipeline_job = client.jobs.create_or_update(
            pipeline_job, experiment_name=experiment_name
        )

        print(f"The job {pipeline_job.name} has been submitted!")
        if output_file is not None:
            with open(output_file, "w") as out_file:
                out_file.write(pipeline_job.name)

        if wait_for_completion == "True":
            total_wait_time = 3600
            current_wait_time = 0
            job_status = [
                "NotStarted",
                "Queued",
                "Starting",
                "Preparing",
                "Running",
                "Finalizing",
                "Provisioning",
                "CancelRequested",
                "Failed",
                "Canceled",
                "NotResponding",
            ]

            while pipeline_job.status in job_status:
                if current_wait_time <= total_wait_time:
                    time.sleep(20)
                    pipeline_job = client.jobs.get(pipeline_job.name)

                    print("Job Status:", pipeline_job.status)

                    current_wait_time = current_wait_time + 15

                    if (
                        pipeline_job.status == "Failed"
                        or pipeline_job.status == "NotResponding"
                        or pipeline_job.status == "CancelRequested"
                        or pipeline_job.status == "Canceled"
                    ):
                        break
                else:
                    break

            if pipeline_job.status == "Completed" or pipeline_job.status == "Finished":
                print("job completed")
            else:
                raise Exception("Sorry, exiting job with failure..")
    except Exception as ex:
        print(
            "Oops! invalid credentials or error while creating ML environment.. Try again...", ex
        )
        raise


def prepare_and_execute(
    subscription_id: str,
    resource_group_name: str,
    workspace_name: str,
    cluster_name: str,
    cluster_size: str,
    cluster_region: str,
    min_instances: int,
    max_instances: int,
    idle_time_before_scale_down: int,
    env_base_image_name: str,
    conda_path: str,
    environment_name: str,
    env_description: str,
    wait_for_completion: str,
    display_name: str,
    experiment_name: str,
    deploy_environment: str,
    build_reference: str,
    model_name: str,
    output_file: str,
    data_config_path: str
):
    """
    Prepares and executes the MLOps pipeline.

    Args:
        subscription_id (str): Azure subscription ID.
        resource_group_name (str): Name of the resource group.
        workspace_name (str): Name of the Azure Machine Learning workspace.
        cluster_name (str): Name of the compute cluster.
        cluster_size (str): Size of the compute cluster.
        cluster_region (str): Region of the compute cluster.
        min_instances (int): Minimum number of instances in the compute cluster.
        max_instances (int): Maximum number of instances in the compute cluster.
        idle_time_before_scale_down (int): Idle time in seconds before scaling down the compute cluster.
        env_base_image_name (str): Name of the base environment image.
        conda_path (str): Path to the conda environment.
        environment_name (str): Name of the environment.
        env_description (str): Description of the environment.
        wait_for_completion (str): Whether to wait for the pipeline execution to complete.
        display_name (str): Display name of the pipeline.
        experiment_name (str): Name of the experiment.
        deploy_environment (str): Environment to deploy the model.
        build_reference (str): Reference for building the model.
        model_name (str): Name of the model.
        output_file (str): Path to the output file.
        data_config_path (str): Path to the data configuration file.
    """
    ml_client = MLClient(
        DefaultAzureCredential(), subscription_id, resource_group_name, workspace_name
    )

    compute = get_compute(
        subscription_id,
        resource_group_name,
        workspace_name,
        cluster_name,
        cluster_size,
        cluster_region,
        min_instances,
        max_instances,
        idle_time_before_scale_down,
    )

    environment = get_environment(
        subscription_id,
        resource_group_name,
        workspace_name,
        env_base_image_name,
        conda_path,
        environment_name,
        env_description,
    )

    print(f"Environment: {environment.name}, version: {environment.version}")

    pipeline_job = construct_pipeline(
        compute.name,
        f"azureml:{environment.name}:{environment.version}",
        display_name,
        deploy_environment,
        build_reference,
        model_name,
        data_config_path,
        ml_client
    )

    execute_pipeline(
        subscription_id,
        resource_group_name,
        workspace_name,
        experiment_name,
        pipeline_job,
        wait_for_completion,
        output_file,
    )


def main():
    """
    Entry point of the MLOps pipeline.
    
    Parses command line arguments and calls the `prepare_and_execute` function
    with the parsed arguments.
    """    
    parser = argparse.ArgumentParser("build_environment")
    parser.add_argument("--subscription_id", type=str, help="Azure subscription id")
    parser.add_argument(
        "--resource_group_name", type=str, help="Azure Machine learning resource group"
    )
    parser.add_argument(
        "--workspace_name", type=str, help="Azure Machine learning Workspace name"
    )
    parser.add_argument(
        "--cluster_name", type=str, help="Azure Machine learning cluster name"
    )
    parser.add_argument(
        "--cluster_size", type=str, help="Azure Machine learning cluster size"
    )
    parser.add_argument(
        "--cluster_region", type=str, help="Azure Machine learning cluster region"
    )
    parser.add_argument("--min_instances", type=int, default=0)
    parser.add_argument("--max_instances", type=int, default=4)
    parser.add_argument("--idle_time_before_scale_down", type=int, default=1800)
    parser.add_argument(
        "--build_reference",
        type=str,
        help="Unique identifier for Azure DevOps pipeline run",
    )
    parser.add_argument(
        "--deploy_environment",
        type=str,
        help="execution and deployment environment. e.g. dev, prod, test",
    )
    parser.add_argument(
        "--experiment_name", type=str, help="Job execution experiment name"
    )
    parser.add_argument("--display_name", type=str, help="Job execution run name")
    parser.add_argument(
        "--wait_for_completion",
        type=str,
        help="determine if pipeline to wait for job completion",
    )
    parser.add_argument(
        "--environment_name",
        type=str,
        help="Azure Machine Learning Environment name for job execution",
    )
    parser.add_argument(
        "--env_base_image_name", type=str, help="Environment custom base image name"
    )
    parser.add_argument(
        "--conda_path", type=str, help="path to conda requirements file"
    )
    parser.add_argument(
        "--env_description", type=str, default="Environment created using Conda."
    )
    parser.add_argument(
        "--model_name", type=str, default="Name used for registration of model"
    )
    parser.add_argument(
        "--output_file", type=str, required=False, help="A file to save run id"
    )
    parser.add_argument("--data_config_path", type=str, required=True, help="data config path")

    args = parser.parse_args()

    prepare_and_execute(
        args.subscription_id,
        args.resource_group_name,
        args.workspace_name,
        args.cluster_name,
        args.cluster_size,
        args.cluster_region,
        args.min_instances,
        args.max_instances,
        args.idle_time_before_scale_down,
        args.env_base_image_name,
        args.conda_path,
        args.environment_name,
        args.env_description,
        args.wait_for_completion,
        args.display_name,
        args.experiment_name,
        args.deploy_environment,
        args.build_reference,
        args.model_name,
        args.output_file,
        args.data_config_path
    )


if __name__ == "__main__":
    main()
